from __future__ import annotations

import json
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchman import license
from watchman import inspector

NOW = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)


def response(*, valid: bool, status_code: int = 200) -> Mock:
    value = Mock()
    value.status_code = status_code
    value.json.return_value = {"valid": valid, "error": None}
    return value


class LicenseValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        license._EMITTED_LIMIT_WARNINGS.clear()

    def test_under_limit_does_not_require_a_license(self) -> None:
        registry = {f"job_{index}": {} for index in range(5)}
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "watchman" / "registry.yaml"
            with patch.object(license, "license_is_valid") as validity_check:
                limited = license.enforce_job_limit(
                    registry,
                    registry_path=registry_path,
                )

        self.assertEqual(list(limited), list(registry))
        validity_check.assert_not_called()

    def test_over_limit_runs_first_five_and_emits_once(self) -> None:
        registry = {f"job_{index}": {} for index in range(7)}
        messages: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "watchman" / "registry.yaml"
            with patch.object(license, "license_is_valid", return_value=False):
                first = license.enforce_job_limit(
                    registry,
                    registry_path=registry_path,
                    emit=messages.append,
                )
                second = license.enforce_job_limit(
                    registry,
                    registry_path=registry_path,
                    emit=messages.append,
                )

        self.assertEqual(list(first), [f"job_{index}" for index in range(5)])
        self.assertEqual(list(second), [f"job_{index}" for index in range(5)])
        self.assertEqual(messages, [license.free_tier_message(7)])

    def test_registry_load_enforces_first_five_without_license(self) -> None:
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
            for index in range(7)
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry_path = root / "watchman" / "registry.yaml"
            registry_path.parent.mkdir()
            registry_path.write_text(
                json.dumps({"jobs": jobs}),
                encoding="utf-8",
            )
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                loaded = inspector.load_registry(registry_path)
                loaded_again = inspector.load_registry(registry_path)

        self.assertEqual(list(loaded), [f"job_{index}" for index in range(5)])
        self.assertEqual(list(loaded_again), list(loaded))
        self.assertEqual(
            stderr.getvalue().count("job(s) exceed the free tier"),
            1,
        )

    def test_valid_license_keeps_all_registered_jobs(self) -> None:
        registry = {f"job_{index}": {} for index in range(7)}
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "watchman" / "registry.yaml"
            with patch.object(license, "license_is_valid", return_value=True):
                limited = license.enforce_job_limit(
                    registry,
                    registry_path=registry_path,
                )

        self.assertEqual(list(limited), list(registry))

    def test_valid_key_uses_form_encoded_license_api_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = Mock()
            session.post.return_value = response(valid=True)

            valid = license.license_is_valid(
                root=root,
                environ={license.LICENSE_ENV_NAME: "valid-key"},
                session=session,
                now=NOW,
            )

        self.assertTrue(valid)
        request = session.post.call_args
        self.assertEqual(request.args[0], license.LICENSE_API_URL)
        self.assertEqual(request.kwargs["data"], {"license_key": "valid-key"})
        self.assertEqual(
            request.kwargs["headers"],
            {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        self.assertNotIn("Authorization", request.kwargs["headers"])

    def test_invalid_key_is_cached_as_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = Mock()
            session.post.return_value = response(valid=False)

            valid = license.license_is_valid(
                root=root,
                environ={license.LICENSE_ENV_NAME: "invalid-key"},
                session=session,
                now=NOW,
            )
            cache = json.loads(
                root.joinpath("data", "license_cache.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertFalse(valid)
        self.assertEqual(cache["validation_status"], "available")
        self.assertFalse(cache["valid"])

    def test_fresh_cache_prevents_revalidation_within_24_hours(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = Mock()
            session.post.return_value = response(valid=True)

            first = license.license_is_valid(
                root=root,
                environ={license.LICENSE_ENV_NAME: "valid-key"},
                session=session,
                now=NOW,
            )
            second = license.license_is_valid(
                root=root,
                environ={license.LICENSE_ENV_NAME: "valid-key"},
                session=session,
                now=NOW + timedelta(hours=23),
            )

        self.assertTrue(first)
        self.assertTrue(second)
        session.post.assert_called_once()

    def test_api_unreachable_uses_last_cached_result_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            available_session = Mock()
            available_session.post.return_value = response(valid=True)
            license.license_is_valid(
                root=root,
                environ={license.LICENSE_ENV_NAME: "valid-key"},
                session=available_session,
                now=NOW,
            )
            unavailable_session = Mock()
            unavailable_session.post.side_effect = requests.ConnectionError(
                "offline"
            )

            valid = license.license_is_valid(
                root=root,
                environ={license.LICENSE_ENV_NAME: "valid-key"},
                session=unavailable_session,
                now=NOW + timedelta(hours=25),
            )
            log_text = root.joinpath("logs", "license.log").read_text(
                encoding="utf-8"
            )

        self.assertTrue(valid)
        self.assertIn("license_api_unreachable using_cached_result", log_text)

    def test_api_unreachable_without_cache_uses_free_tier_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = Mock()
            session.post.side_effect = requests.ConnectionError("offline")

            valid = license.license_is_valid(
                root=root,
                environ={license.LICENSE_ENV_NAME: "new-key"},
                session=session,
                now=NOW,
            )
            cache = json.loads(
                root.joinpath("data", "license_cache.json").read_text(
                    encoding="utf-8"
                )
            )
            log_text = root.joinpath("logs", "license.log").read_text(
                encoding="utf-8"
            )

        self.assertFalse(valid)
        self.assertEqual(cache["validation_status"], "unavailable")
        self.assertNotIn("valid", cache)
        self.assertIn("no_cached_result using_free_tier", log_text)


if __name__ == "__main__":
    unittest.main()
