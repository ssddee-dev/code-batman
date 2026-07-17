from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchman import notifier


def dossier() -> dict:
    return {
        "what_was_flagged": [
            {
                "flag_code": "schema_mismatch",
                "details": "The observed schema differs from the registry.",
                "source_ids": ["/evidence/prices.csv"],
            }
        ],
        "what_the_evidence_shows": [],
        "suspected_areas": [
            {
                "area": "CSV header handling",
                "likelihood": "plausible",
                "rationale": "The first line is blank.",
                "source_ids": ["/evidence/prices.csv:L1-L3"],
            }
        ],
        "not_checked": [],
        "human_decision_needed": {
            "question": "Which bounded action, if any, should be approved?",
            "options": [
                {
                    "action_id": "rerun_only",
                    "description": "Run the price job once more.",
                    "risk_note": "The header may remain absent.",
                },
                {
                    "action_id": "none",
                    "description": "Take no execution action.",
                    "risk_note": "The mismatch remains present.",
                },
            ],
        },
    }


def write_inputs(root: Path, *, flag_count: int = 1) -> tuple[Path, Path]:
    dossier_path = root / "fetch_prices_20260717_120000_000000.json"
    dossier_path.write_text(json.dumps(dossier()), encoding="utf-8")
    history_path = root / "history.jsonl"
    flags = [
        {
            "code": f"flag_{index}",
            "observed": {"value": index},
            "reference": {"minimum": index + 1},
        }
        for index in range(flag_count)
    ]
    flags[0]["code"] = "schema_mismatch"
    flags[0]["observed"] = []
    flags[0]["reference"] = ["timestamp", "symbol", "price_usd"]
    history_path.write_text(
        json.dumps({"job": "fetch_prices", "flags": flags}) + "\n",
        encoding="utf-8",
    )
    return dossier_path, history_path


class NotifierTests(unittest.TestCase):
    def test_message_contains_required_summary_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dossier_path, history_path = write_inputs(Path(temp_dir))

            message = notifier.format_dossier_message(
                dossier_path, history_path=history_path
            )

        self.assertIn("Job: fetch_prices", message)
        self.assertIn(f"Dossier: {dossier_path.name}", message)
        self.assertIn("schema_mismatch: observed=[]", message)
        self.assertIn('reference=["timestamp", "symbol", "price_usd"]', message)
        self.assertIn("CSV header handling (plausible)", message)
        self.assertIn("Which bounded action", message)
        self.assertIn("1. [rerun_only]", message)
        self.assertIn("Risk: The header may remain absent.", message)
        self.assertIn("2. [none]", message)
        self.assertLessEqual(len(message), notifier.TELEGRAM_MESSAGE_LIMIT)

    def test_long_evidence_is_explicitly_truncated_within_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dossier_path, history_path = write_inputs(
                Path(temp_dir), flag_count=20
            )
            payload = json.loads(dossier_path.read_text(encoding="utf-8"))
            payload["human_decision_needed"]["question"] = "Q" * 2_000
            dossier_path.write_text(json.dumps(payload), encoding="utf-8")

            message = notifier.format_dossier_message(
                dossier_path,
                history_path=history_path,
                max_chars=900,
            )

        self.assertLessEqual(len(message), 900)
        self.assertTrue(message.endswith(notifier.TRUNCATION_MARKER))

    @patch("watchman.notifier.requests.post")
    def test_send_dossier_posts_plain_text_with_mocked_http(
        self, post: Mock
    ) -> None:
        response = Mock()
        post.return_value = response
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dossier_path, history_path = write_inputs(root)
            with patch.dict(
                os.environ,
                {
                    "TELEGRAM_BOT_TOKEN": "test-token",
                    "TELEGRAM_CHAT_ID": "test-chat",
                },
                clear=True,
            ):
                notifier.send_dossier(
                    dossier_path, root=root, history_path=history_path
                )

        post.assert_called_once()
        request_url = post.call_args.args[0]
        request_json = post.call_args.kwargs["json"]
        self.assertEqual(
            request_url,
            "https://api.telegram.org/bottest-token/sendMessage",
        )
        self.assertEqual(request_json["chat_id"], "test-chat")
        self.assertNotIn("parse_mode", request_json)
        buttons = request_json["reply_markup"]["inline_keyboard"]
        self.assertEqual(
            [row[0]["text"] for row in buttons],
            ["rerun_only", "none"],
        )
        self.assertEqual(
            buttons[0][0]["callback_data"],
            f"rerun_only:{dossier_path.name}",
        )
        self.assertNotIn(str(dossier_path.parent), buttons[0][0]["callback_data"])
        self.assertTrue(
            all(
                len(row[0]["callback_data"].encode("utf-8"))
                <= notifier.CALLBACK_DATA_LIMIT_BYTES
                for row in buttons
            )
        )
        self.assertLessEqual(
            len(request_json["text"]), notifier.TELEGRAM_MESSAGE_LIMIT
        )
        response.raise_for_status.assert_called_once_with()

    @patch("watchman.notifier.requests.post")
    def test_missing_configuration_fails_before_http(self, post: Mock) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dossier_path, history_path = write_inputs(Path(temp_dir))
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(
                    notifier.TelegramConfigurationError,
                    "TELEGRAM_BOT_TOKEN",
                ):
                    notifier.send_dossier(
                        dossier_path,
                        root=Path(temp_dir),
                        history_path=history_path,
                    )

        post.assert_not_called()

    def test_keyboard_rejects_callback_data_over_64_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / f"fetch_prices_{'x' * 50}.json"
            path.write_text(json.dumps(dossier()), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "64-byte"):
                notifier.build_inline_keyboard(path)

    def test_keyboard_rejects_unscoped_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fetch_prices_short.json"
            payload = dossier()
            payload["human_decision_needed"]["options"][0][
                "action_id"
            ] = "restart"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unsupported dossier action"):
                notifier.build_inline_keyboard(path)


if __name__ == "__main__":
    unittest.main()
