"""Reliable Lemon Squeezy license validation with local outage fallback."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
LICENSE_API_URL = "https://api.lemonsqueezy.com/v1/licenses/validate"
LICENSE_ENV_NAME = "NIGHT_WATCHMAN_LICENSE_KEY"
CACHE_TTL = timedelta(hours=24)
CACHE_PATH = ROOT / "data" / "license_cache.json"
LOG_PATH = ROOT / "logs" / "license.log"

CacheRecord = dict[str, Any]


class LicenseAPIUnavailable(RuntimeError):
    """Raised when the remote service cannot provide a validation result."""


def _utc_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _fingerprint(license_key: str) -> str:
    return hashlib.sha256(license_key.encode("utf-8")).hexdigest()


def _configured_license_key(
    *,
    root: Path,
    environ: Mapping[str, str],
) -> str | None:
    environment_value = environ.get(LICENSE_ENV_NAME)
    if isinstance(environment_value, str) and environment_value.strip():
        return environment_value.strip()
    env_path = root / ".env"
    if not env_path.exists():
        return None
    file_value = dotenv_values(env_path).get(LICENSE_ENV_NAME)
    if not isinstance(file_value, str) or not file_value.strip():
        return None
    if file_value.strip().lower().startswith("your_"):
        return None
    return file_value.strip()


def _append_log(path: Path, message: str, *, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"{now.isoformat()} {message}\n")


def _read_cache(path: Path) -> CacheRecord | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_cache(path: Path, record: CacheRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _cached_result(cache: CacheRecord) -> bool | None:
    if cache.get("validation_status") != "available":
        return None
    valid = cache.get("valid")
    return valid if isinstance(valid, bool) else None


def _validate_remotely(license_key: str, *, session: Any) -> bool:
    try:
        response = session.post(
            LICENSE_API_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"license_key": license_key},
            timeout=15,
        )
    except requests.RequestException as error:
        raise LicenseAPIUnavailable("license_api_request_unavailable") from error

    status_code = response.status_code
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return False
    if not isinstance(status_code, int) or status_code >= 500:
        raise LicenseAPIUnavailable("license_api_response_unavailable")
    try:
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        raise LicenseAPIUnavailable("license_api_response_unavailable") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("valid"), bool):
        raise LicenseAPIUnavailable("license_api_payload_unavailable")
    return payload["valid"]


def license_is_valid(
    *,
    root: Path = ROOT,
    environ: Mapping[str, str] = os.environ,
    session: Any = requests,
    now: datetime | None = None,
    cache_path: Path | None = None,
    log_path: Path | None = None,
) -> bool:
    """Return licensed status, using cached evidence when validation is down."""
    checked_at = _utc_now(now)
    resolved_cache_path = cache_path or root / "data" / "license_cache.json"
    resolved_log_path = log_path or root / "logs" / "license.log"
    license_key = _configured_license_key(root=root, environ=environ)
    if license_key is None:
        return False

    fingerprint = _fingerprint(license_key)
    cache = _read_cache(resolved_cache_path)
    if cache is not None and cache.get("key_fingerprint") != fingerprint:
        cache = None

    if cache is not None:
        last_attempt = _parse_timestamp(cache.get("last_attempt_at"))
        if last_attempt is not None and checked_at - last_attempt < CACHE_TTL:
            cached = _cached_result(cache)
            if cache.get("last_attempt_outcome") == "api_unreachable":
                _append_log(
                    resolved_log_path,
                    "license_api_unreachable using_cached_result"
                    if cached is not None
                    else "license_api_unreachable no_cached_result using_free_tier",
                    now=checked_at,
                )
            return cached if cached is not None else False

    try:
        valid = _validate_remotely(license_key, session=session)
    except LicenseAPIUnavailable:
        cached = _cached_result(cache or {})
        record = dict(cache or {})
        record.update(
            {
                "key_fingerprint": fingerprint,
                "last_attempt_at": checked_at.isoformat(),
                "last_attempt_outcome": "api_unreachable",
            }
        )
        if cached is None:
            record["validation_status"] = "unavailable"
            record.pop("valid", None)
            record.pop("validated_at", None)
        _write_cache(resolved_cache_path, record)
        _append_log(
            resolved_log_path,
            "license_api_unreachable using_cached_result"
            if cached is not None
            else "license_api_unreachable no_cached_result using_free_tier",
            now=checked_at,
        )
        return cached if cached is not None else False

    _write_cache(
        resolved_cache_path,
        {
            "key_fingerprint": fingerprint,
            "validation_status": "available",
            "valid": valid,
            "validated_at": checked_at.isoformat(),
            "last_attempt_at": checked_at.isoformat(),
            "last_attempt_outcome": "api_available",
        },
    )
    return valid
