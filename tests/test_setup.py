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
from watchman import license


def telegram_session(
    chat_id: int = 12345,
    *,
    update_batches: list[list[dict]] | None = None,
    token_validity: list[bool] | None = None,
) -> Mock:
    session = Mock()
    batches = list(
        update_batches
        if update_batches is not None
        else [[{"message": {"chat": {"id": chat_id}}}]]
    )
    validity = list(token_validity or [])

    def get(url: str, **kwargs: object) -> Mock:
        response = Mock()
        if url.endswith("/getMe"):
            is_valid = validity.pop(0) if validity else True
            response.json.return_value = {
                "ok": is_valid,
                "result": {"id": 1} if is_valid else None,
            }
        else:
            updates = batches.pop(0) if batches else []
            response.json.return_value = {"ok": True, "result": updates}
        return response

    post_response = Mock()
    post_response.json.return_value = {"ok": True, "result": {}}
    session.get.side_effect = get
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
        self.assertEqual(session.get.call_count, 1)
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
        self.assertEqual(session.get.call_count, 3)
        session.post.assert_called_once()

    def test_auto_detection_labels_missing_update_explicitly(self) -> None:
        session = telegram_session(update_batches=[[]])

        with self.assertRaisesRegex(setup.SetupError, "No new Telegram message"):
            setup.auto_detect_telegram_chat_id("token", session=session)

        request = session.get.call_args
        self.assertTrue(request.args[0].endswith("/getUpdates"))
        self.assertEqual(
            request.kwargs["params"],
            {"timeout": 0, "allowed_updates": ["message"]},
        )
        self.assertEqual(request.kwargs["timeout"], 20)

    def test_test_message_requires_telegram_ok_payload(self) -> None:
        session = telegram_session()
        session.post.return_value.json.return_value = {
            "ok": False,
            "description": "chat unavailable",
        }

        with self.assertRaisesRegex(setup.SetupError, "did not confirm"):
            setup.send_telegram_test("token", "chat", session=session)

    def test_mid_wizard_failure_preserves_values_and_rerun_skips_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_session = telegram_session(update_batches=[[]])
            first_secrets = Mock(
                side_effect=["saved-openai-secret", "saved-bot-token"]
            )

            with self.assertRaises(setup.SetupCanceled):
                setup.configure_credentials(
                    root=root,
                    environ={},
                    secret_input=first_secrets,
                    input_function=Mock(side_effect=["yes", "", "q"]),
                    output=Mock(),
                    session=first_session,
                )

            env_after_failure = root.joinpath(".env").read_text(encoding="utf-8")
            rerun_session = telegram_session(chat_id=24680)
            rerun_secrets = Mock(
                side_effect=AssertionError(
                    "saved API and bot credentials must be skipped"
                )
            )
            rerun_output = Mock()
            configured = setup.configure_credentials(
                root=root,
                environ={},
                secret_input=rerun_secrets,
                input_function=Mock(side_effect=["yes", ""]),
                output=rerun_output,
                session=rerun_session,
            )

        self.assertIn("OPENAI_API_KEY=saved-openai-secret", env_after_failure)
        self.assertIn("TELEGRAM_BOT_TOKEN=saved-bot-token", env_after_failure)
        self.assertNotIn("TELEGRAM_CHAT_ID", env_after_failure)
        self.assertEqual(configured["TELEGRAM_CHAT_ID"], "24680")
        rerun_secrets.assert_not_called()
        rendered = " ".join(
            call.args[0] for call in rerun_output.call_args_list
        )
        self.assertIn("OpenAI API key: already configured", rendered)
        self.assertIn("Telegram bot token: already configured", rendered)

    def test_auto_detection_retries_after_a_new_message(self) -> None:
        session = telegram_session(
            chat_id=-777,
            update_batches=[[], [{"message": {"chat": {"id": -777}}}]],
        )
        output = Mock()

        with tempfile.TemporaryDirectory() as temp_dir:
            configured = setup.configure_credentials(
                root=Path(temp_dir),
                environ={},
                secret_input=Mock(
                    side_effect=["openai-secret", "valid-bot-token"]
                ),
                input_function=Mock(side_effect=["yes", "", "r", ""]),
                output=output,
                session=session,
            )

        self.assertEqual(configured["TELEGRAM_CHAT_ID"], "-777")
        rendered = " ".join(call.args[0] for call in output.call_args_list)
        self.assertIn("Send a NEW message", rendered)
        self.assertIn("bot token is valid", rendered)
        self.assertIn("problem is only the missing message", rendered)

    def test_get_me_distinguishes_invalid_token_from_missing_message(self) -> None:
        session = telegram_session(
            update_batches=[[]],
            token_validity=[False, True, True],
        )
        output = Mock()
        secret_input = Mock(
            side_effect=[
                "openai-secret",
                "invalid-bot-token",
                "valid-bot-token",
                "manual-chat",
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            configured = setup.configure_credentials(
                root=root,
                environ={},
                secret_input=secret_input,
                input_function=Mock(side_effect=["yes", "", "m"]),
                output=output,
                session=session,
            )
            env_text = root.joinpath(".env").read_text(encoding="utf-8")

        self.assertEqual(configured["TELEGRAM_BOT_TOKEN"], "valid-bot-token")
        self.assertEqual(configured["TELEGRAM_CHAT_ID"], "manual-chat")
        self.assertIn("TELEGRAM_BOT_TOKEN=valid-bot-token", env_text)
        self.assertNotIn("invalid-bot-token", env_text)
        rendered = " ".join(call.args[0] for call in output.call_args_list)
        self.assertIn("bot token is invalid", rendered)
        self.assertIn("bot token is valid", rendered)
        self.assertIn("problem is only the missing message", rendered)
        prompts = " ".join(call.args[0] for call in secret_input.call_args_list)
        self.assertIn("@BotFather", prompts)
        self.assertIn("123456:ABC-DEF", prompts)


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

    def test_sixth_job_is_blocked_with_free_tier_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry = root / "watchman" / "registry.yaml"
            registry.parent.mkdir()
            jobs = [
                {
                    "name": f"job_{index}",
                    "command": f"python job_{index}.py",
                    "output": f"job_{index}.txt",
                    "expectations": {
                        "min_size_bytes": 1,
                        "expected_frequency_seconds": 60,
                    },
                }
                for index in range(5)
            ]
            original = yaml.safe_dump({"jobs": jobs}, sort_keys=False)
            registry.write_text(original, encoding="utf-8")
            sixth = {
                "name": "job_5",
                "command": "python job_5.py",
                "output": "job_5.txt",
                "expectations": {
                    "min_size_bytes": 1,
                    "expected_frequency_seconds": 60,
                },
            }

            with self.assertRaisesRegex(
                setup.SetupError,
                "1 job\\(s\\) exceed the free tier",
            ) as raised:
                setup.append_job_declaration(sixth, registry_path=registry)

            resulting = registry.read_text(encoding="utf-8")

        self.assertEqual(str(raised.exception), license.free_tier_message(6))
        self.assertEqual(resulting, original)

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
