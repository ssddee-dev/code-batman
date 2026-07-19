"""Long-poll Telegram approvals for bounded actions and re-inspection."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests
from dotenv import load_dotenv

from watchman.executor import ACTION_IDS, execute
from watchman.inspector import inspect_one
from watchman.notifier import (
    BUTTON_ACTION_IDS,
    CALLBACK_DATA_LIMIT_BYTES,
    TELEGRAM_MESSAGE_LIMIT,
    TelegramConfigurationError,
    job_name_from_dossier_filename,
)

ROOT = Path(__file__).resolve().parents[1]
APPROVER_LOG_PATH = ROOT / "logs" / "approver.log"
HANDLED_STATE_PATH = ROOT / "logs" / "handled_dossiers.jsonl"
POLL_TIMEOUT_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 35
TRUNCATION_MARKER = "(truncated, see full evidence)"

Evidence = dict[str, Any]
ExecuteFunction = Callable[..., Evidence]
InspectFunction = Callable[..., Evidence]


def parse_callback_data(data: Any) -> tuple[str, str, str]:
    """Return validated action, dossier filename, and scoped job."""
    if not isinstance(data, str):
        raise ValueError("callback_data must be a string")
    if len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT_BYTES:
        raise ValueError("callback_data exceeds 64 bytes")
    action_id, separator, dossier_filename = data.partition(":")
    if not separator or action_id not in BUTTON_ACTION_IDS:
        raise ValueError("callback_data has an unsupported action")
    if (
        not dossier_filename
        or Path(dossier_filename).name != dossier_filename
        or not dossier_filename.endswith(".json")
    ):
        raise ValueError("callback_data has an invalid dossier filename")
    job_name = job_name_from_dossier_filename(dossier_filename)
    return action_id, dossier_filename, job_name


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _configure_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"night_watchman.approver.{log_path}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s")
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def _bounded_text(lines: list[str]) -> str:
    complete = "\n".join(lines)
    if len(complete) <= TELEGRAM_MESSAGE_LIMIT:
        return complete
    budget = TELEGRAM_MESSAGE_LIMIT - len(TRUNCATION_MARKER) - 1
    selected: list[str] = []
    for line in lines:
        candidate = "\n".join([*selected, line])
        if len(candidate) > budget:
            break
        selected.append(line)
    message = "\n".join(selected)
    if not message:
        message = complete[:budget].rstrip()
    return f"{message}\n{TRUNCATION_MARKER}"


def _render_raw(value: Any, limit: int = 180) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(rendered) <= limit:
        return rendered
    return rendered[: limit - 1].rstrip() + "…"


def _dossier_offers_action(dossier_path: Path, action_id: str) -> bool:
    try:
        dossier = json.loads(dossier_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    decision = (
        dossier.get("human_decision_needed")
        if isinstance(dossier, dict)
        else None
    )
    options = decision.get("options") if isinstance(decision, dict) else None
    if not isinstance(options, list):
        return False
    return any(
        isinstance(option, dict) and option.get("action_id") == action_id
        for option in options
    )


def format_action_result(
    action_record: Evidence, inspection: Evidence
) -> str:
    """Return a compact action record and raw re-inspection outcome."""
    lines = [
        f"Action: {action_record.get('action', 'unavailable')}",
        f"Job: {action_record.get('job', 'unavailable')}",
        f"Started: {action_record.get('started_at', 'unavailable')}",
        f"Completed: {action_record.get('completed_at', 'unavailable')}",
    ]
    moved = action_record.get("files_moved")
    if isinstance(moved, dict) and moved.get("status") == "available":
        items = moved.get("items")
        if isinstance(items, list) and items:
            lines.append("Files moved:")
            for item in items[:3]:
                if isinstance(item, dict):
                    lines.append(
                        f"- {item.get('from', 'unavailable')} → "
                        f"{item.get('to', 'unavailable')}"
                    )
            if len(items) > 3:
                lines.append(TRUNCATION_MARKER)
        else:
            lines.append(
                "Files moved: none "
                f"({moved.get('reason', 'no move recorded')})"
            )
    elif isinstance(moved, dict):
        lines.append(
            "Files moved: unavailable "
            f"({moved.get('reason', 'reason unavailable')})"
        )
    else:
        lines.append("Files moved: unavailable (record missing)")

    exit_status = action_record.get("job_exit_status")
    if isinstance(exit_status, dict) and exit_status.get("status") == "available":
        lines.append(f"Job exit status: {exit_status.get('exit_code')}")
    elif isinstance(exit_status, dict):
        lines.append(
            "Job exit status: unavailable "
            f"({exit_status.get('reason', 'reason unavailable')})"
        )
    else:
        lines.append("Job exit status: unavailable (record missing)")

    flags = inspection.get("flags")
    if isinstance(flags, list) and not flags:
        lines.append("Re-inspection: flags cleared")
    elif isinstance(flags, list):
        lines.append(f"Re-inspection: {len(flags)} flag(s) remaining")
        for flag in flags[:5]:
            if not isinstance(flag, dict):
                continue
            lines.append(
                f"- {flag.get('code', 'unavailable')}: "
                f"observed={_render_raw(flag.get('observed', 'unavailable'))}; "
                f"reference={_render_raw(flag.get('reference', 'unavailable'))}"
            )
        if len(flags) > 5:
            lines.append(TRUNCATION_MARKER)
    else:
        lines.append("Re-inspection: unavailable (flags missing)")
    return _bounded_text(lines)


class Approver:
    """Poll, authorize once per dossier, execute, and re-inspect."""

    def __init__(
        self,
        *,
        token: str,
        chat_id: str,
        root: Path = ROOT,
        session: Any = requests,
        execute_function: ExecuteFunction = execute,
        inspect_function: InspectFunction = inspect_one,
        handled_state_path: Path | None = None,
        log_path: Path | None = None,
    ) -> None:
        self.token = token
        self.chat_id = str(chat_id)
        self.root = root
        self.session = session
        self.execute_function = execute_function
        self.inspect_function = inspect_function
        self.handled_state_path = (
            handled_state_path or root / "logs" / "handled_dossiers.jsonl"
        )
        self.logger = _configure_logger(
            log_path or root / "logs" / "approver.log"
        )
        self.offset = 0
        self.handled_dossiers = self._load_handled_dossiers()

    @property
    def api_base(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def _load_handled_dossiers(self) -> set[str]:
        handled: set[str] = set()
        if not self.handled_state_path.exists():
            return handled
        try:
            with self.handled_state_path.open(encoding="utf-8") as state_file:
                for line in state_file:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    dossier_filename = record.get("dossier_filename")
                    if isinstance(dossier_filename, str):
                        handled.add(dossier_filename)
        except (OSError, UnicodeError):
            return handled
        return handled

    def _mark_handled(self, dossier_filename: str, action_id: str) -> None:
        self.handled_state_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "dossier_filename": dossier_filename,
            "action_id": action_id,
            "approved_at": _utc_now(),
        }
        with self.handled_state_path.open("a", encoding="utf-8") as state_file:
            state_file.write(json.dumps(record, sort_keys=True) + "\n")
        self.handled_dossiers.add(dossier_filename)

    def _post(
        self,
        method: str,
        payload: Evidence,
        *,
        timeout: int = 20,
    ) -> Any:
        response = self.session.post(
            f"{self.api_base}/{method}",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        return response

    def answer_callback(self, callback_query_id: str, text: str) -> None:
        self._post(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id, "text": text},
        )

    def edit_approved_message(
        self, message: Evidence, action_id: str
    ) -> None:
        original_text = message.get("text")
        if not isinstance(original_text, str):
            original_text = "Night Watchman evidence dossier"
        suffix = f"\n\n→ {action_id} approved"
        if len(original_text) + len(suffix) > TELEGRAM_MESSAGE_LIMIT:
            original_text = original_text[
                : TELEGRAM_MESSAGE_LIMIT - len(suffix)
            ].rstrip()
        self._post(
            "editMessageText",
            {
                "chat_id": message["chat"]["id"],
                "message_id": message["message_id"],
                "text": original_text + suffix,
                "reply_markup": {"inline_keyboard": []},
            },
        )

    def send_message(self, text: str) -> None:
        self._post(
            "sendMessage",
            {"chat_id": self.chat_id, "text": text},
        )

    def _log_callback(
        self,
        callback_query: Evidence,
        *,
        status: str,
        action_id: str | None = None,
        dossier_filename: str | None = None,
    ) -> None:
        message = callback_query.get("message")
        chat = message.get("chat") if isinstance(message, dict) else {}
        self.logger.info(
            json.dumps(
                {
                    "callback_query_id": callback_query.get("id", "unavailable"),
                    "chat_id": (
                        chat.get("id", "unavailable")
                        if isinstance(chat, dict)
                        else "unavailable"
                    ),
                    "action_id": action_id or "unavailable",
                    "dossier_filename": dossier_filename or "unavailable",
                    "status": status,
                    "handled_at": _utc_now(),
                },
                sort_keys=True,
            )
        )

    def handle_callback(self, callback_query: Evidence) -> None:
        query_id = callback_query.get("id")
        message = callback_query.get("message")
        chat = message.get("chat") if isinstance(message, dict) else None
        incoming_chat_id = chat.get("id") if isinstance(chat, dict) else None
        if not isinstance(query_id, str):
            self._log_callback(callback_query, status="ignored_missing_query_id")
            return
        if str(incoming_chat_id) != self.chat_id:
            self.answer_callback(query_id, "not authorized")
            self._log_callback(callback_query, status="ignored_other_chat")
            return

        try:
            action_id, dossier_filename, job_name = parse_callback_data(
                callback_query.get("data")
            )
        except ValueError:
            self.answer_callback(query_id, "invalid request")
            self._log_callback(callback_query, status="ignored_invalid_data")
            return

        if dossier_filename in self.handled_dossiers:
            self.answer_callback(query_id, "already handled")
            self._log_callback(
                callback_query,
                status="already_handled",
                action_id=action_id,
                dossier_filename=dossier_filename,
            )
            return

        dossier_path = self.root / "dossiers" / dossier_filename
        if not dossier_path.is_file():
            self.answer_callback(query_id, "dossier unavailable")
            self._log_callback(
                callback_query,
                status="ignored_missing_dossier",
                action_id=action_id,
                dossier_filename=dossier_filename,
            )
            return

        if not _dossier_offers_action(dossier_path, action_id):
            self.answer_callback(query_id, "action not offered")
            self._log_callback(
                callback_query,
                status="ignored_action_not_offered",
                action_id=action_id,
                dossier_filename=dossier_filename,
            )
            return

        self.answer_callback(query_id, f"{action_id} approved")
        self._mark_handled(dossier_filename, action_id)
        if isinstance(message, dict):
            try:
                self.edit_approved_message(message, action_id)
            except requests.RequestException:
                self._log_callback(
                    callback_query,
                    status="approved_message_edit_unavailable",
                    action_id=action_id,
                    dossier_filename=dossier_filename,
                )

        if action_id == "none":
            self._log_callback(
                callback_query,
                status="handled_no_action",
                action_id=action_id,
                dossier_filename=dossier_filename,
            )
            try:
                self.send_message("No action taken. Evidence retained.")
            except requests.RequestException:
                self._log_callback(
                    callback_query,
                    status="handled_no_action_reply_unavailable",
                    action_id=action_id,
                    dossier_filename=dossier_filename,
                )
            return

        try:
            if action_id not in ACTION_IDS:
                raise ValueError(f"action is not executable: {action_id}")
            action_record = self.execute_function(
                action_id,
                job_name,
                root=self.root,
            )
            inspection = self.inspect_function(
                job_name,
                root=self.root,
                registry_path=self.root / "watchman" / "registry.yaml",
                history_path=self.root / "watchman" / "history.jsonl",
            )
            result_message = format_action_result(action_record, inspection)
            status = "handled"
        except Exception as error:
            result_message = (
                "Approved action processing unavailable: "
                f"{type(error).__name__}: {error}. "
                "No re-inspection outcome is available."
            )
            status = "handled_processing_unavailable"
        self._log_callback(
            callback_query,
            status=status,
            action_id=action_id,
            dossier_filename=dossier_filename,
        )
        try:
            self.send_message(result_message)
        except requests.RequestException:
            self._log_callback(
                callback_query,
                status=f"{status}_reply_unavailable",
                action_id=action_id,
                dossier_filename=dossier_filename,
            )

    def poll_once(self) -> int:
        # Telegram persists allowed_updates between getUpdates calls. Declare
        # callback_query every time so setup's message filter cannot leak here.
        response = self._post(
            "getUpdates",
            {
                "offset": self.offset,
                "timeout": POLL_TIMEOUT_SECONDS,
                "allowed_updates": ["callback_query"],
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError("Telegram getUpdates response is unavailable")
        updates = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(updates, list):
            raise RuntimeError("Telegram getUpdates result is unavailable")
        for update in updates:
            if not isinstance(update, dict):
                continue
            update_id = update.get("update_id")
            callback_query = update.get("callback_query")
            if isinstance(callback_query, dict):
                self.handle_callback(callback_query)
            if isinstance(update_id, int):
                self.offset = max(self.offset, update_id + 1)
        return len(updates)

    def run_forever(self) -> None:
        """Long poll until Ctrl-C while preserving offset and single-use state."""
        try:
            while True:
                try:
                    self.poll_once()
                except (requests.RequestException, RuntimeError) as error:
                    self.logger.error(
                        "poll_unavailable error_type=%s error=%s",
                        type(error).__name__,
                        error,
                    )
                    time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("approver_stopped_by_keyboard_interrupt")


def main() -> int:
    """Load Telegram configuration and run the approval loop until Ctrl-C."""
    load_dotenv(dotenv_path=ROOT / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", token),
            ("TELEGRAM_CHAT_ID", chat_id),
        )
        if not value
    ]
    if missing:
        raise TelegramConfigurationError(
            f"missing Telegram configuration: {', '.join(missing)}"
        )
    Approver(token=token, chat_id=chat_id).run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
