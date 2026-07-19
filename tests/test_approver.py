from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchman import approver


def callback(
    *,
    query_id: str = "query-1",
    chat_id: str = "chat-1",
    action_id: str = "rerun_only",
    filename: str = "fetch_prices_20260717_120000_000000.json",
) -> dict:
    return {
        "id": query_id,
        "data": f"{action_id}:{filename}",
        "message": {
            "message_id": 42,
            "text": "Original dossier message",
            "chat": {"id": chat_id},
        },
    }


def available_action_record(action_id: str = "rerun_only") -> dict:
    return {
        "action": action_id,
        "job": "fetch_prices",
        "files_moved": {
            "status": "available",
            "items": [],
            "reason": "action_does_not_move_files",
        },
        "job_exit_status": {"status": "available", "exit_code": 0},
        "started_at": "2026-07-17T12:00:00+00:00",
        "completed_at": "2026-07-17T12:00:01+00:00",
    }


class ApproverTests(unittest.TestCase):
    def make_approver(
        self,
        root: Path,
        *,
        session: Mock | None = None,
        execute_function: Mock | None = None,
        inspect_function: Mock | None = None,
    ) -> tuple[approver.Approver, Mock, Mock, Mock]:
        http = session or Mock()
        if session is None:
            response = Mock()
            response.json.return_value = {"ok": True, "result": []}
            http.post.return_value = response
        execute_mock = execute_function or Mock(
            return_value=available_action_record()
        )
        inspect_mock = inspect_function or Mock(return_value={"flags": []})
        instance = approver.Approver(
            token="test-token",
            chat_id="chat-1",
            root=root,
            session=http,
            execute_function=execute_mock,
            inspect_function=inspect_mock,
        )
        return instance, http, execute_mock, inspect_mock

    @staticmethod
    def create_dossier(
        root: Path,
        filename: str,
        actions: tuple[str, ...] = (
            "quarantine_and_rerun",
            "rerun_only",
            "none",
        ),
    ) -> None:
        dossier_path = root / "dossiers" / filename
        dossier_path.parent.mkdir(parents=True, exist_ok=True)
        dossier_path.write_text(
            json.dumps(
                {
                    "human_decision_needed": {
                        "options": [
                            {"action_id": action_id}
                            for action_id in actions
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_callback_parser_accepts_generic_timestamped_job_payload(self) -> None:
        parsed = approver.parse_callback_data(
            "quarantine_and_rerun:"
            "third_job_20260717_120000_000000.json"
        )
        self.assertEqual(
            parsed,
            (
                "quarantine_and_rerun",
                "third_job_20260717_120000_000000.json",
                "third_job",
            ),
        )

    def test_callback_parser_rejects_path_and_unsupported_action(self) -> None:
        with self.assertRaises(ValueError):
            approver.parse_callback_data(
                "rerun_only:../fetch_prices_20260717.json"
            )
        with self.assertRaises(ValueError):
            approver.parse_callback_data(
                "restart:fetch_prices_20260717.json"
            )

    def test_single_use_guard_blocks_second_press_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            filename = "fetch_prices_20260717_120000_000000.json"
            self.create_dossier(root, filename)
            instance, http, execute_mock, inspect_mock = self.make_approver(root)

            instance.handle_callback(callback(filename=filename))
            instance.handle_callback(
                callback(query_id="query-2", filename=filename)
            )

            restarted, _, restarted_execute, _ = self.make_approver(root)
            restarted.handle_callback(
                callback(query_id="query-3", filename=filename)
            )
            log_text = (root / "logs" / "approver.log").read_text(
                encoding="utf-8"
            )

        execute_mock.assert_called_once()
        inspect_mock.assert_called_once()
        restarted_execute.assert_not_called()
        self.assertIn('"callback_query_id": "query-1"', log_text)
        self.assertIn('"callback_query_id": "query-2"', log_text)
        answer_payloads = [
            call.kwargs["json"]
            for call in http.post.call_args_list
            if call.args[0].endswith("/answerCallbackQuery")
        ]
        self.assertEqual(answer_payloads[1]["text"], "already handled")

    def test_none_answers_edits_and_sends_no_action_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            filename = "backup_db_20260717_120000_000000.json"
            self.create_dossier(root, filename)
            instance, http, execute_mock, inspect_mock = self.make_approver(root)

            instance.handle_callback(
                callback(
                    action_id="none",
                    filename=filename,
                )
            )

        execute_mock.assert_not_called()
        inspect_mock.assert_not_called()
        methods = [call.args[0].rsplit("/", 1)[-1] for call in http.post.call_args_list]
        self.assertEqual(
            methods,
            ["answerCallbackQuery", "editMessageText", "sendMessage"],
        )
        self.assertEqual(
            http.post.call_args_list[-1].kwargs["json"]["text"],
            "No action taken. Evidence retained.",
        )

    def test_other_chat_never_executes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            instance, http, execute_mock, inspect_mock = self.make_approver(root)

            instance.handle_callback(callback(chat_id="other-chat"))

        execute_mock.assert_not_called()
        inspect_mock.assert_not_called()
        self.assertEqual(len(http.post.call_args_list), 1)
        self.assertEqual(
            http.post.call_args.kwargs["json"]["text"], "not authorized"
        )

    def test_action_not_offered_by_dossier_never_executes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            filename = "fetch_prices_20260717_120000_000000.json"
            self.create_dossier(root, filename, actions=("none",))
            instance, http, execute_mock, inspect_mock = self.make_approver(root)

            instance.handle_callback(callback(filename=filename))

        execute_mock.assert_not_called()
        inspect_mock.assert_not_called()
        self.assertEqual(len(http.post.call_args_list), 1)
        self.assertEqual(
            http.post.call_args.kwargs["json"]["text"],
            "action not offered",
        )

    def test_action_reply_reports_remaining_raw_flags(self) -> None:
        inspection = {
            "flags": [
                {
                    "code": "schema_mismatch",
                    "observed": [],
                    "reference": ["timestamp", "symbol", "price_usd"],
                }
            ]
        }
        inspect_mock = Mock(return_value=inspection)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            filename = "fetch_prices_20260717_120000_000000.json"
            self.create_dossier(root, filename)
            instance, http, _, _ = self.make_approver(
                root, inspect_function=inspect_mock
            )

            instance.handle_callback(callback(filename=filename))

        result_text = http.post.call_args_list[-1].kwargs["json"]["text"]
        self.assertIn("Started: 2026-07-17T12:00:00+00:00", result_text)
        self.assertIn("Completed: 2026-07-17T12:00:01+00:00", result_text)
        self.assertIn("Re-inspection: 1 flag(s) remaining", result_text)
        self.assertIn("observed=[]", result_text)
        self.assertIn(
            'reference=["timestamp", "symbol", "price_usd"]',
            result_text,
        )

    def test_polling_uses_timeout_and_advances_offset(self) -> None:
        response = Mock()
        response.json.return_value = {
            "ok": True,
            "result": [{"update_id": 9}, {"update_id": 12}],
        }
        session = Mock()
        session.post.return_value = response
        with tempfile.TemporaryDirectory() as temp_dir:
            instance, _, _, _ = self.make_approver(
                Path(temp_dir), session=session
            )

            count = instance.poll_once()

        self.assertEqual(count, 2)
        self.assertEqual(instance.offset, 13)
        request = session.post.call_args
        self.assertTrue(request.args[0].endswith("/getUpdates"))
        self.assertEqual(request.kwargs["json"]["timeout"], 30)
        self.assertEqual(request.kwargs["json"]["offset"], 0)
        self.assertEqual(
            request.kwargs["json"]["allowed_updates"], ["callback_query"]
        )
        self.assertEqual(request.kwargs["timeout"], 35)

    def test_polling_does_not_advance_offset_when_callback_handling_fails(
        self,
    ) -> None:
        response = Mock()
        response.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 9,
                    "callback_query": callback(),
                }
            ],
        }
        session = Mock()
        session.post.return_value = response
        with tempfile.TemporaryDirectory() as temp_dir:
            instance, _, _, _ = self.make_approver(
                Path(temp_dir), session=session
            )
            with patch.object(
                instance,
                "handle_callback",
                side_effect=requests.ConnectionError("temporary"),
            ):
                with self.assertRaises(requests.ConnectionError):
                    instance.poll_once()

        self.assertEqual(instance.offset, 0)


if __name__ == "__main__":
    unittest.main()
