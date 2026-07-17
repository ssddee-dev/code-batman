"""Execute only the two human-approved Night Watchman action types."""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ACTION_IDS = {"quarantine_and_rerun", "rerun_only"}
JOB_SPECS = {
    "fetch_prices": {
        "script": "jobs/fetch_prices.py",
        "output_pattern": "data/prices.csv",
    },
    "backup_db": {
        "script": "jobs/backup_db.py",
        "output_pattern": "backups/demo_db_*.tar.gz",
    },
}

ActionRecord = dict[str, Any]


def _utc_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.strftime("%Y%m%d_%H%M%S_%f")


def _job_spec(job: str) -> dict[str, str]:
    spec = JOB_SPECS.get(job)
    if spec is None:
        raise ValueError(f"unsupported job: {job}")
    return spec


def _current_output(job: str, root: Path) -> tuple[Path | None, str]:
    pattern = _job_spec(job)["output_pattern"]
    candidates = [path for path in root.glob(pattern) if path.is_file()]
    if not candidates:
        return None, pattern
    return (
        max(candidates, key=lambda path: (path.stat().st_mtime_ns, str(path))),
        pattern,
    )


def _run_job(
    job: str,
    *,
    root: Path,
    python_executable: str,
) -> dict[str, Any]:
    script_path = root / _job_spec(job)["script"]
    started_at = _utc_now()
    try:
        result = subprocess.run(
            [python_executable, str(script_path)],
            cwd=root,
            check=False,
        )
    except OSError as error:
        return {
            "status": "unavailable",
            "reason": "job_process_unavailable",
            "error": str(error),
            "script_path": str(script_path),
            "started_at": started_at.isoformat(),
            "completed_at": _utc_now().isoformat(),
        }
    return {
        "status": "available",
        "exit_code": result.returncode,
        "script_path": str(script_path),
        "started_at": started_at.isoformat(),
        "completed_at": _utc_now().isoformat(),
    }


def rerun_only(
    job: str,
    *,
    root: Path = ROOT,
    python_executable: str = sys.executable,
    now: datetime | None = None,
) -> ActionRecord:
    """Rerun one scoped job and return its sourced execution record."""
    _job_spec(job)
    action_started = _utc_now(now)
    job_status = _run_job(
        job, root=root, python_executable=python_executable
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
    python_executable: str = sys.executable,
    now: datetime | None = None,
) -> ActionRecord:
    """Move the current output without deletion, rerun, and return evidence."""
    _job_spec(job)
    action_started = _utc_now(now)
    output_path, pattern = _current_output(job, root)

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
        shutil.move(str(output_path), str(destination))
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
        job, root=root, python_executable=python_executable
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
    python_executable: str = sys.executable,
) -> ActionRecord:
    """Dispatch exactly one of the two declared executable actions."""
    if action_id == "quarantine_and_rerun":
        return quarantine_and_rerun(
            job, root=root, python_executable=python_executable
        )
    if action_id == "rerun_only":
        return rerun_only(
            job, root=root, python_executable=python_executable
        )
    raise ValueError(f"unsupported action: {action_id}")
