"""Reliable multi-issuer license validation with local outage fallback."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
LEMON_ISSUER = "lemon_squeezy"
POLAR_ISSUER = "polar"
LICENSE_API_URL = "https://api.lemonsqueezy.com/v1/licenses/validate"
LICENSE_ENV_NAME = "NIGHT_WATCHMAN_LICENSE_KEY"
POLAR_LICENSE_API_URL = (
    "https://api.polar.sh/v1/customer-portal/license-keys/validate"
)
POLAR_LICENSE_ENV_NAME = "POLAR_LICENSE_KEY"
POLAR_ORGANIZATION_ID = "89d9e3d9-b790-4a11-a5ed-bf6cdd146344"
CACHE_TTL = timedelta(hours=24)
CACHE_PATH = ROOT / "data" / "license_cache.json"
LOG_PATH = ROOT / "logs" / "license.log"
FREE_JOB_LIMIT = 5
LEMON_CHECKOUT_URL = (
    "https://ssddeelabs.lemonsqueezy.com/checkout/buy/"
    "03a57721-0a8f-46fb-a139-159dfc69599e"
)
POLAR_CHECKOUT_URL = (
    "https://buy.polar.sh/"
    "polar_cl_tPvwnkU2WokWVdmIGHLBJu7AY8cVpqqiGYG591JGda5"
)
# Backwards-compatible name for callers that linked the original checkout.
CHECKOUT_URL = LEMON_CHECKOUT_URL

_EMITTED_LIMIT_WARNINGS: set[str] = set()

CacheRecord = dict[str, Any]


class LicenseAPIUnavailable(RuntimeError):
    """Raised when the remote service cannot provide a validation result."""


def _utc_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _fingerprint(license_key: str) -> str:
    return hashlib.sha256(license_key.encode("utf-8")).hexdigest()


def _configured_license(
    *,
    root: Path,
    environ: Mapping[str, str],
) -> tuple[str, str] | None:
    env_path = root / ".env"
    file_values = dotenv_values(env_path) if env_path.exists() else {}

    def configured_value(name: str) -> str | None:
        value = environ.get(name)
        if not isinstance(value, str) or not value.strip():
            value = file_values.get(name)
        if not isinstance(value, str) or not value.strip():
            return None
        resolved = value.strip()
        return None if resolved.lower().startswith("your_") else resolved

    lemon_key = configured_value(LICENSE_ENV_NAME)
    if lemon_key is not None:
        return LEMON_ISSUER, lemon_key
    polar_key = configured_value(POLAR_LICENSE_ENV_NAME)
    if polar_key is not None:
        return POLAR_ISSUER, polar_key
    return None


def _append_log(path: Path, message: str, *, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"{now.isoformat()} {message}\n")


def _safe_append_log(path: Path, message: str, *, now: datetime) -> None:
    try:
        _append_log(path, message, now=now)
    except OSError:
        pass


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


def _safe_write_cache(path: Path, record: CacheRecord) -> None:
    try:
        _write_cache(path, record)
    except OSError:
        pass


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


def _validate_remotely(
    issuer: str,
    license_key: str,
    *,
    session: Any,
) -> bool:
    try:
        if issuer == LEMON_ISSUER:
            response = session.post(
                LICENSE_API_URL,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"license_key": license_key},
                timeout=15,
            )
        elif issuer == POLAR_ISSUER:
            response = session.post(
                POLAR_LICENSE_API_URL,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json={
                    "key": license_key,
                    "organization_id": POLAR_ORGANIZATION_ID,
                },
                timeout=15,
            )
        else:
            raise LicenseAPIUnavailable("unknown_license_issuer")
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
    if not isinstance(payload, dict):
        raise LicenseAPIUnavailable("license_api_payload_unavailable")
    if issuer == LEMON_ISSUER:
        if not isinstance(payload.get("valid"), bool):
            raise LicenseAPIUnavailable("license_api_payload_unavailable")
        return payload["valid"]
    status = payload.get("status")
    if not isinstance(status, str):
        raise LicenseAPIUnavailable("license_api_payload_unavailable")
    return status == "granted"


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
    configured = _configured_license(root=root, environ=environ)
    if configured is None:
        return False
    issuer, license_key = configured

    fingerprint = _fingerprint(license_key)
    cache = _read_cache(resolved_cache_path)
    if cache is not None and (
        cache.get("issuer") != issuer
        or cache.get("key_fingerprint") != fingerprint
    ):
        cache = None

    if cache is not None:
        last_attempt = _parse_timestamp(cache.get("last_attempt_at"))
        if last_attempt is not None and checked_at - last_attempt < CACHE_TTL:
            cached = _cached_result(cache)
            if cache.get("last_attempt_outcome") == "api_unreachable":
                _safe_append_log(
                    resolved_log_path,
                    f"license_api_unreachable using_cached_result issuer={issuer}"
                    if cached is not None
                    else (
                        "license_api_unreachable no_cached_result "
                        f"using_free_tier issuer={issuer}"
                    ),
                    now=checked_at,
                )
            return cached if cached is not None else False

    try:
        valid = _validate_remotely(issuer, license_key, session=session)
    except LicenseAPIUnavailable:
        cached = _cached_result(cache or {})
        record = dict(cache or {})
        record.update(
            {
                "issuer": issuer,
                "key_fingerprint": fingerprint,
                "last_attempt_at": checked_at.isoformat(),
                "last_attempt_outcome": "api_unreachable",
            }
        )
        if cached is None:
            record["validation_status"] = "unavailable"
            record.pop("valid", None)
            record.pop("validated_at", None)
        _safe_write_cache(resolved_cache_path, record)
        _safe_append_log(
            resolved_log_path,
            f"license_api_unreachable using_cached_result issuer={issuer}"
            if cached is not None
            else (
                "license_api_unreachable no_cached_result "
                f"using_free_tier issuer={issuer}"
            ),
            now=checked_at,
        )
        return cached if cached is not None else False

    _safe_write_cache(
        resolved_cache_path,
        {
            "issuer": issuer,
            "key_fingerprint": fingerprint,
            "validation_status": "available",
            "valid": valid,
            "validated_at": checked_at.isoformat(),
            "last_attempt_at": checked_at.isoformat(),
            "last_attempt_outcome": "api_available",
        },
    )
    return valid


def free_tier_message(job_count: int) -> str:
    """Return the exact free-tier notice for a declared job count."""
    excess = max(0, job_count - FREE_JOB_LIMIT)
    return (
        f"{excess} job(s) exceed the free tier ({FREE_JOB_LIMIT}). "
        "Unlimited jobs:\n"
        f"Lemon Squeezy: {LEMON_CHECKOUT_URL}\n"
        f"Polar: {POLAR_CHECKOUT_URL}"
    )


def _project_root_for_registry(registry_path: Path) -> Path:
    if registry_path.parent.name == "watchman":
        return registry_path.parent.parent
    return registry_path.parent


def enforce_job_limit(
    registry: dict[str, CacheRecord],
    *,
    registry_path: Path,
    emit: Any | None = None,
) -> dict[str, CacheRecord]:
    """Return all licensed jobs or the first five free jobs, warning once."""
    if len(registry) <= FREE_JOB_LIMIT:
        return registry
    root = _project_root_for_registry(registry_path)
    try:
        licensed = license_is_valid(root=root)
    except Exception:
        licensed = False
        _safe_append_log(
            root / "logs" / "license.log",
            "license_check_unavailable using_free_tier",
            now=_utc_now(),
        )
    if licensed:
        return registry

    warning_key = str(registry_path.resolve())
    if warning_key not in _EMITTED_LIMIT_WARNINGS:
        message = free_tier_message(len(registry))
        if emit is None:
            print(message, file=sys.stderr)
        else:
            emit(message)
        _EMITTED_LIMIT_WARNINGS.add(warning_key)
    return dict(list(registry.items())[:FREE_JOB_LIMIT])
