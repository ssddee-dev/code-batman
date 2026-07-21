from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchman import license

NOW = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)


def response(*, valid: bool, status_code: int = 200) -> Mock:
    value = Mock()
    value.status_code = status_code
    value.json.return_value = {"valid": valid, "error": None}
    return value


class LicenseValidationTests(unittest.TestCase):
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
