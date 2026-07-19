from __future__ import annotations

import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchman import setup


def telegram_session(chat_id: int = 12345) -> Mock:
    session = Mock()
    get_response = Mock()
    get_response.json.return_value = {
        "ok": True,
        "result": [{"message": {"chat": {"id": chat_id}}}],
    }
    post_response = Mock()
    post_response.json.return_value = {"ok": True, "result": {}}
    session.get.return_value = get_response
    session.post.return_value = post_response
    return session


class SetupCredentialTests(unittest.TestCase):
    def test_runtime_check_reports_old_python_and_missing_dependencies(self) -> None:
        problems = setup.check_python_and_dependencies(
            version_info=(3, 10, 9),
            find_spec=lambda module: None if module == "openai" else object(),
        )

        self.assertTrue(any("Python 3.11" in problem for problem in problems))
        self.assertTrue(any("openai" in problem for problem in problems))

    def test_existing_values_are_skipped_without_secret_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath(".env").write_text(
                "OPENAI_API_KEY=existing-openai\n"
                "TELEGRAM_BOT_TOKEN=existing-bot\n"
                "TELEGRAM_CHAT_ID=existing-chat\n",
                encoding="utf-8",
            )
            session = telegram_session()
            secret_input = Mock(side_effect=AssertionError("must not prompt"))
            output = Mock()

            configured = setup.configure_credentials(
                root=root,
                environ={},
                secret_input=secret_input,
                input_function=Mock(),
                output=output,
                session=session,
            )

        self.assertEqual(configured["TELEGRAM_CHAT_ID"], "existing-chat")
        secret_input.assert_not_called()
        session.get.assert_not_called()
        session.post.assert_called_once()
        rendered_output = " ".join(call.args[0] for call in output.call_args_list)
        self.assertNotIn("existing-openai", rendered_output)
        self.assertNotIn("existing-bot", rendered_output)

    def test_missing_values_are_hidden_persisted_and_chat_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = telegram_session(chat_id=-98765)
            secret_input = Mock(
                side_effect=["new-openai-secret", "new-telegram-secret"]
            )
            input_function = Mock(side_effect=["yes", ""])
            output = Mock()

            configured = setup.configure_credentials(
                root=root,
                environ={},
                secret_input=secret_input,
                input_function=input_function,
                output=output,
                session=session,
            )
            env_path = root / ".env"
            env_text = env_path.read_text(encoding="utf-8")
            mode = stat.S_IMODE(env_path.stat().st_mode)

        self.assertEqual(configured["TELEGRAM_CHAT_ID"], "-98765")
        self.assertIn("OPENAI_API_KEY=new-openai-secret", env_text)
        self.assertIn("TELEGRAM_BOT_TOKEN=new-telegram-secret", env_text)
        self.assertIn("TELEGRAM_CHAT_ID=-98765", env_text)
        self.assertEqual(mode, stat.S_IRUSR | stat.S_IWUSR)
        rendered_output = " ".join(call.args[0] for call in output.call_args_list)
        self.assertNotIn("new-openai-secret", rendered_output)
        self.assertNotIn("new-telegram-secret", rendered_output)
        session.get.assert_called_once()
        session.post.assert_called_once()

    def test_auto_detection_labels_missing_update_explicitly(self) -> None:
        session = telegram_session()
        session.get.return_value.json.return_value = {"ok": True, "result": []}

        with self.assertRaisesRegex(setup.SetupError, "No Telegram chat"):
            setup.auto_detect_telegram_chat_id("token", session=session)

    def test_test_message_requires_telegram_ok_payload(self) -> None:
        session = telegram_session()
        session.post.return_value.json.return_value = {
            "ok": False,
            "description": "chat unavailable",
        }

        with self.assertRaisesRegex(setup.SetupError, "did not confirm"):
            setup.send_telegram_test("token", "chat", session=session)


class SetupRegistryTests(unittest.TestCase):
    def test_csv_job_prompt_collects_optional_expectations(self) -> None:
        answers = iter(
            [
                "name that is too long",
                "daily_report",
                "/opt/reporting/export.sh --daily",
                "artifacts/report_*.csv",
                "logs/report.log",
                "100",
                "86400",
                "2",
                "timestamp, account_id, total",
            ]
        )
        output = Mock()

        declaration = setup.prompt_job_declaration(
            input_function=lambda prompt: next(answers),
            output=output,
        )

        self.assertEqual(declaration["name"], "daily_report")
        self.assertEqual(declaration["expectations"]["min_rows"], 2)
        self.assertEqual(
            declaration["expectations"]["schema"],
            ["timestamp", "account_id", "total"],
        )
        self.assertTrue(
            any(
                "Telegram callback limit" in call.args[0]
                for call in output.call_args_list
            )
        )

    def test_append_preserves_existing_registry_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = Path(temp_dir) / "watchman" / "registry.yaml"
            registry.parent.mkdir()
            original = (
                "jobs:\n"
                "  - name: existing\n"
                "    command: python existing.py\n"
                "    output: existing.txt\n"
                "    expectations:\n"
                "      min_size_bytes: 1\n"
                "      expected_frequency_seconds: 60\n"
            )
            registry.write_text(original, encoding="utf-8")
            declaration = {
                "name": "new_job",
                "command": "python new.py",
                "output": "new.jsonl",
                "expectations": {
                    "min_size_bytes": 2,
                    "min_rows": 1,
                    "expected_frequency_seconds": 120,
                },
            }

            setup.append_job_declaration(
                declaration,
                registry_path=registry,
            )
            resulting_text = registry.read_text(encoding="utf-8")
            payload = yaml.safe_load(resulting_text)

        self.assertTrue(resulting_text.startswith(original))
        self.assertEqual(
            [job["name"] for job in payload["jobs"]],
            ["existing", "new_job"],
        )

    def test_append_rejects_duplicate_without_modifying_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = Path(temp_dir) / "registry.yaml"
            original = (
                "jobs:\n"
                "  - name: duplicate\n"
                "    command: python first.py\n"
                "    output: first.txt\n"
                "    expectations:\n"
                "      min_size_bytes: 1\n"
                "      expected_frequency_seconds: 60\n"
            )
            registry.write_text(original, encoding="utf-8")
            declaration = {
                "name": "duplicate",
                "command": "python second.py",
                "output": "second.txt",
                "expectations": {
                    "min_size_bytes": 1,
                    "expected_frequency_seconds": 60,
                },
            }

            with self.assertRaisesRegex(setup.SetupError, "duplicate job"):
                setup.append_job_declaration(
                    declaration,
                    registry_path=registry,
                )

            self.assertEqual(registry.read_text(encoding="utf-8"), original)

    def test_next_steps_end_with_approver_and_cron_commands(self) -> None:
        output = Mock()

        setup.print_next_steps(
            root=Path("/srv/night watchman"),
            output=output,
        )

        lines = [call.args[0] for call in output.call_args_list]
        self.assertIn("watchman.approver", lines[-2])
        self.assertIn("watchman.inspector --quiet", lines[-1])
        self.assertIn("watchman.investigator --notify", lines[-1])


if __name__ == "__main__":
    unittest.main()
