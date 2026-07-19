"""Execute the two approved actions using generic registry declarations."""

from __future__ import annotations

import shutil
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from watchman.inspector import latest_output, load_registry

ROOT = Path(__file__).resolve().parents[1]
ACTION_IDS = {"quarantine_and_rerun", "rerun_only"}

ActionRecord = dict[str, Any]


def _utc_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.strftime("%Y%m%d_%H%M%S_%f")


def _registry_path(root: Path, registry_path: Path | None) -> Path:
    return registry_path or root / "watchman" / "registry.yaml"


def _job_declaration(
    job: str, root: Path, registry_path: Path | None
) -> tuple[dict[str, Any], Path]:
    path = _registry_path(root, registry_path)
    declaration = load_registry(path).get(job)
    if declaration is None:
        raise ValueError(f"job is not declared in registry: {job}")
    return declaration, path


def _command_parts(command: str | list[str]) -> list[str]:
    return shlex.split(command) if isinstance(command, str) else list(command)


def _run_job(
    declaration: dict[str, Any],
    *,
    root: Path,
    registry_path: Path,
) -> dict[str, Any]:
    command = _command_parts(declaration["command"])
    started_at = _utc_now()
    try:
        result = subprocess.run(
            command,
            cwd=root,
            check=False,
        )
    except OSError as error:
        return {
            "status": "unavailable",
            "reason": "job_process_unavailable",
            "error": str(error),
            "command": command,
            "source": {"path": str(registry_path), "job": declaration["name"]},
            "started_at": started_at.isoformat(),
            "completed_at": _utc_now().isoformat(),
        }
    return {
        "status": "available",
        "exit_code": result.returncode,
        "command": command,
        "source": {"path": str(registry_path), "job": declaration["name"]},
        "started_at": started_at.isoformat(),
        "completed_at": _utc_now().isoformat(),
    }


def rerun_only(
    job: str,
    *,
    root: Path = ROOT,
    registry_path: Path | None = None,
    now: datetime | None = None,
) -> ActionRecord:
    """Rerun a registered job command and return its sourced execution record."""
    declaration, resolved_registry_path = _job_declaration(
        job, root, registry_path
    )
    action_started = _utc_now(now)
    job_status = _run_job(
        declaration,
        root=root,
        registry_path=resolved_registry_path,
    )
    return {
        "action": "rerun_only",
        "job": job,
        "started_at": action_started.isoformat(),
        "completed_at": _utc_now().isoformat(),
        "files_moved": {
            "status": "available",
            "items": [],
            "reason": "action_does_not_move_files",
        },
        "job_exit_status": job_status,
    }


def quarantine_and_rerun(
    job: str,
    *,
    root: Path = ROOT,
    registry_path: Path | None = None,
    now: datetime | None = None,
) -> ActionRecord:
    """Move the current output without deletion, rerun, and return evidence."""
    declaration, resolved_registry_path = _job_declaration(
        job, root, registry_path
    )
    action_started = _utc_now(now)
    pattern = declaration["output"]
    output_path = latest_output(root, pattern)

    if output_path is None:
        moved: dict[str, Any] = {
            "status": "unavailable",
            "items": [],
            "reason": "current_output_missing",
            "source": {
                "path": str(root / pattern),
                "pattern": pattern,
            },
        }
    else:
        quarantine_dir = root / "quarantine"
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        destination = quarantine_dir / (
            f"{output_path.name}.{_timestamp(action_started)}"
        )
        try:
            shutil.move(str(output_path), str(destination))
        except OSError as error:
            return {
                "action": "quarantine_and_rerun",
                "job": job,
                "started_at": action_started.isoformat(),
                "completed_at": _utc_now().isoformat(),
                "files_moved": {
                    "status": "unavailable",
                    "items": [],
                    "reason": "quarantine_move_failed",
                    "error": str(error),
                    "source": {"path": str(output_path)},
                    "intended_destination": {"path": str(destination)},
                },
                "job_exit_status": {
                    "status": "unavailable",
                    "reason": "rerun_not_attempted_after_quarantine_failure",
                },
            }
        else:
            moved = {
                "status": "available",
                "items": [
                    {
                        "from": str(output_path),
                        "to": str(destination),
                        "moved_at": _utc_now().isoformat(),
                    }
                ],
            }

    job_status = _run_job(
        declaration,
        root=root,
        registry_path=resolved_registry_path,
    )
    return {
        "action": "quarantine_and_rerun",
        "job": job,
        "started_at": action_started.isoformat(),
        "completed_at": _utc_now().isoformat(),
        "files_moved": moved,
        "job_exit_status": job_status,
    }


def execute(
    action_id: str,
    job: str,
    *,
    root: Path = ROOT,
    registry_path: Path | None = None,
) -> ActionRecord:
    """Dispatch exactly one of the two declared executable actions."""
    if action_id == "quarantine_and_rerun":
        return quarantine_and_rerun(
            job, root=root, registry_path=registry_path
        )
    if action_id == "rerun_only":
        return rerun_only(
            job, root=root, registry_path=registry_path
        )
    raise ValueError(f"unsupported action: {action_id}")
