"""Send compact, text-only evidence dossier summaries through Telegram."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / "watchman" / "history.jsonl"
TELEGRAM_MESSAGE_LIMIT = 4096
TRUNCATION_MARKER = "(truncated, see full dossier)"
SCOPED_JOBS = ("fetch_prices", "backup_db")

Evidence = dict[str, Any]


class TelegramConfigurationError(RuntimeError):
    """Raised when required Telegram environment settings are unavailable."""


def _job_name_from_path(dossier_path: Path) -> str:
    for job_name in SCOPED_JOBS:
        if dossier_path.stem.startswith(f"{job_name}_"):
            return job_name
    return "unavailable"


def _latest_flags(history_path: Path, job_name: str) -> list[Evidence] | None:
    if not history_path.exists():
        return None
    latest: Evidence | None = None
    try:
        with history_path.open(encoding="utf-8") as history_file:
            for line in history_file:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("job") == job_name:
                    latest = record
    except (OSError, UnicodeError):
        return None
    if latest is None or not isinstance(latest.get("flags"), list):
        return None
    return latest["flags"]


def _compact_text(value: Any, limit: int) -> tuple[str, bool]:
    if isinstance(value, str):
        rendered = " ".join(value.split())
    else:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(rendered) <= limit:
        return rendered, False
    return rendered[: max(0, limit - 1)].rstrip() + "…", True


def _fallback_flags(dossier: Evidence) -> list[Evidence]:
    items = dossier.get("what_was_flagged")
    if not isinstance(items, list):
        return []
    unavailable = "unavailable (matching inspection evidence not found)"
    flags: list[Evidence] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        flags.append(
            {
                "code": item.get("flag_code", "unavailable"),
                "observed": unavailable,
                "reference": unavailable,
            }
        )
    return flags


def _flag_line(flag: Evidence) -> tuple[str, bool]:
    code, code_truncated = _compact_text(
        flag.get("code", flag.get("flag_code", "unavailable")), 100
    )
    observed, observed_truncated = _compact_text(
        flag.get("observed", "unavailable"), 180
    )
    reference, reference_truncated = _compact_text(
        flag.get("reference", "unavailable"), 180
    )
    return (
        f"- {code}: observed={observed}; reference={reference}",
        code_truncated or observed_truncated or reference_truncated,
    )


def _bounded_message(
    lines: list[str], *, max_chars: int, already_truncated: bool
) -> str:
    if max_chars <= len(TRUNCATION_MARKER) + 1:
        raise ValueError("max_chars is too small for the truncation marker")

    complete = "\n".join(lines)
    if len(complete) <= max_chars and not already_truncated:
        return complete

    budget = max_chars - len(TRUNCATION_MARKER) - 1
    selected: list[str] = []
    for line in lines:
        candidate = "\n".join([*selected, line])
        if len(candidate) > budget:
            break
        selected.append(line)

    message = "\n".join(selected).rstrip()
    if not message:
        message = complete[:budget].rstrip()
    return f"{message}\n{TRUNCATION_MARKER}"


def format_dossier_message(
    dossier_path: Path | str,
    *,
    history_path: Path = HISTORY_PATH,
    max_chars: int = TELEGRAM_MESSAGE_LIMIT,
) -> str:
    """Return a bounded Telegram message containing raw flag evidence."""
    path = Path(dossier_path)
    try:
        dossier = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"unable to read dossier {path}: {error}") from error
    if not isinstance(dossier, dict):
        raise ValueError(f"dossier must contain a JSON object: {path}")

    job_name = _job_name_from_path(path)
    flags = _latest_flags(history_path, job_name) or _fallback_flags(dossier)
    was_truncated = False

    lines = [
        "Night Watchman evidence dossier",
        f"Job: {job_name}",
        f"Dossier: {path.name}",
        "",
        "Flags:",
    ]
    if flags:
        first_flag, truncated = _flag_line(flags[0])
        lines.append(first_flag)
        was_truncated = was_truncated or truncated
    else:
        lines.append("- unavailable (no flag evidence found)")

    suspected = dossier.get("suspected_areas")
    if isinstance(suspected, list) and suspected and isinstance(suspected[0], dict):
        area, area_truncated = _compact_text(
            suspected[0].get("area", "unavailable"), 240
        )
        likelihood, likelihood_truncated = _compact_text(
            suspected[0].get("likelihood", "unavailable"), 40
        )
        lines.extend(["", f"Top suspected area: {area} ({likelihood})"])
        was_truncated = (
            was_truncated or area_truncated or likelihood_truncated
        )
    else:
        lines.extend(["", "Top suspected area: unavailable"])

    decision = dossier.get("human_decision_needed")
    if isinstance(decision, dict):
        question, question_truncated = _compact_text(
            decision.get("question", "unavailable"), 350
        )
        lines.extend(["", f"Human decision needed: {question}", "Options:"])
        was_truncated = was_truncated or question_truncated
        options = decision.get("options")
        if isinstance(options, list) and options:
            for index, option in enumerate(options[:3], start=1):
                if not isinstance(option, dict):
                    continue
                action_id, action_truncated = _compact_text(
                    option.get("action_id", "unavailable"), 50
                )
                description, description_truncated = _compact_text(
                    option.get("description", "unavailable"), 220
                )
                risk_note, risk_truncated = _compact_text(
                    option.get("risk_note", "unavailable"), 220
                )
                lines.append(f"{index}. [{action_id}] {description}")
                lines.append(f"   Risk: {risk_note}")
                was_truncated = was_truncated or any(
                    (action_truncated, description_truncated, risk_truncated)
                )
            if len(options) > 3:
                was_truncated = True
        else:
            lines.append("1. unavailable (no decision options found)")
    else:
        lines.extend(
            [
                "",
                "Human decision needed: unavailable",
                "Options:",
                "1. unavailable (decision section not found)",
            ]
        )

    if len(flags) > 1:
        lines.extend(["", "Additional flags:"])
        for flag in flags[1:3]:
            flag_line, flag_truncated = _flag_line(flag)
            lines.append(flag_line)
            was_truncated = was_truncated or flag_truncated
        if len(flags) > 3:
            was_truncated = True

    return _bounded_message(
        lines, max_chars=max_chars, already_truncated=was_truncated
    )


def send_dossier(
    dossier_path: Path | str,
    *,
    root: Path = ROOT,
    history_path: Path | None = None,
) -> None:
    """Send one bounded dossier summary to the configured Telegram chat."""
    load_dotenv(dotenv_path=root / ".env")
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

    message = format_dossier_message(
        dossier_path,
        history_path=history_path or root / "watchman" / "history.jsonl",
    )
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=20,
    )
    response.raise_for_status()
