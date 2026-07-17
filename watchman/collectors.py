"""Deterministic evidence collectors for the two Night Watchman demo jobs."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / "watchman" / "history.jsonl"
REGISTRY_PATH = ROOT / "watchman" / "registry.yaml"

Evidence = dict[str, Any]


def _source(
    path: Path,
    *,
    line_start: int | None = None,
    line_end: int | None = None,
    detail: str | None = None,
) -> Evidence:
    source: Evidence = {"path": str(path)}
    source_id = str(path)
    if line_start is not None:
        source["line_start"] = line_start
        source["line_end"] = line_end if line_end is not None else line_start
        source_id = f"{source_id}:L{source['line_start']}-L{source['line_end']}"
    if detail is not None:
        source["detail"] = detail
        source_id = f"{source_id}#{detail}"
    source["source_id"] = source_id
    return source


def _available(value: Any, source: Evidence) -> Evidence:
    return {"status": "available", "value": value, "source": source}


def _unavailable(reason: str, source: Evidence) -> Evidence:
    return {"status": "unavailable", "reason": reason, "source": source}


def _read_lines(path: Path) -> tuple[list[str] | None, Evidence | None]:
    source = _source(path)
    if not path.exists():
        return None, _unavailable("source_missing", source)
    try:
        return path.read_text(encoding="utf-8").splitlines(), None
    except (OSError, UnicodeError) as error:
        source["error"] = str(error)
        return None, _unavailable("source_unreadable", source)


def _tail_lines(path: Path, count: int) -> Evidence:
    lines, error = _read_lines(path)
    if error is not None:
        return error
    assert lines is not None
    if not lines:
        return _unavailable("source_empty", _source(path))
    start_index = max(0, len(lines) - count)
    return _available(
        lines[start_index:],
        _source(path, line_start=start_index + 1, line_end=len(lines)),
    )


def _sample_lines(path: Path, *, first: int, last: int) -> tuple[Evidence, Evidence]:
    lines, error = _read_lines(path)
    if error is not None:
        return error, error.copy()
    assert lines is not None
    if not lines:
        unavailable = _unavailable("source_empty", _source(path))
        return unavailable, unavailable.copy()

    head_end = min(first, len(lines))
    tail_start = max(1, len(lines) - last + 1)
    head = _available(
        lines[:head_end],
        _source(path, line_start=1, line_end=head_end),
    )
    tail = _available(
        lines[tail_start - 1 :],
        _source(path, line_start=tail_start, line_end=len(lines)),
    )
    return head, tail


def _history_records(
    history_path: Path,
) -> tuple[list[tuple[int, Evidence]], list[Evidence]]:
    lines, read_error = _read_lines(history_path)
    if read_error is not None:
        return [], [read_error]
    assert lines is not None
    records: list[tuple[int, Evidence]] = []
    issues: list[Evidence] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            source = _source(
                history_path, line_start=line_number, line_end=line_number
            )
            source["error"] = str(error)
            issues.append(_unavailable("history_line_invalid_json", source))
            continue
        if isinstance(value, dict) and isinstance(value.get("job"), str):
            records.append((line_number, value))
    return records, issues


def _metric_from_record(
    record: Evidence, metric: str, history_path: Path, line_number: int
) -> Evidence:
    source = _source(history_path, line_start=line_number, line_end=line_number)
    observed = record.get("observed")
    if not isinstance(observed, dict):
        return _unavailable("inspection_observations_missing", source)
    measurement = observed.get(metric)
    if not isinstance(measurement, dict) or measurement.get("status") != "available":
        return _unavailable(f"{metric}_unavailable_in_inspection", source)
    return _available(measurement.get("value"), source)


def _fetch_history_trend(history_path: Path) -> tuple[Evidence, list[Evidence]]:
    records, issues = _history_records(history_path)
    selected = [
        (line_number, record)
        for line_number, record in records
        if record.get("job") == "fetch_prices"
    ][-10:]
    if not selected:
        return (
            _unavailable(
                "no_fetch_prices_inspections",
                _source(history_path),
            ),
            issues,
        )

    points = []
    for line_number, record in selected:
        points.append(
            {
                "inspected_at": record.get("inspected_at", "unavailable"),
                "size_bytes": _metric_from_record(
                    record, "size_bytes", history_path, line_number
                ),
                "row_count": _metric_from_record(
                    record, "row_count", history_path, line_number
                ),
                "source": _source(
                    history_path, line_start=line_number, line_end=line_number
                ),
            }
        )
    return _available(
        points,
        _source(
            history_path,
            line_start=selected[0][0],
            line_end=selected[-1][0],
            detail="last_10_fetch_prices_inspections",
        ),
    ), issues


def _archive_count_trend(history_path: Path) -> tuple[Evidence, list[Evidence]]:
    records, issues = _history_records(history_path)
    backup_records = [
        (line_number, record)
        for line_number, record in records
        if record.get("job") == "backup_db"
    ]
    if not backup_records:
        return (
            _unavailable("no_backup_db_inspections", _source(history_path)),
            issues,
        )

    distinct_paths: set[str] = set()
    points: list[Evidence] = []
    for line_number, record in backup_records:
        output = record.get("output")
        if isinstance(output, dict) and output.get("status") == "available":
            path = output.get("path")
            if isinstance(path, str):
                distinct_paths.add(path)
        points.append(
            {
                "inspected_at": record.get("inspected_at", "unavailable"),
                "observed_distinct_archive_paths": len(distinct_paths),
                "source": _source(
                    history_path, line_start=line_number, line_end=line_number
                ),
            }
        )
    selected = points[-10:]
    return _available(
        selected,
        _source(
            history_path,
            line_start=backup_records[-len(selected)][0],
            line_end=backup_records[-1][0],
            detail="last_10_backup_db_inspections",
        ),
    ), issues


def _registry_line_range(registry_path: Path, job_name: str) -> tuple[int, int] | None:
    lines, error = _read_lines(registry_path)
    if error is not None or lines is None:
        return None
    marker = f"  {job_name}:"
    for index, line in enumerate(lines):
        if line == marker:
            end = len(lines)
            for candidate in range(index + 1, len(lines)):
                if lines[candidate].startswith("  ") and not lines[
                    candidate
                ].startswith("    "):
                    end = candidate
                    break
            return index + 1, end
    return None


def _registry_expectations(registry_path: Path, job_name: str) -> Evidence:
    source = _source(registry_path)
    if not registry_path.exists():
        return _unavailable("registry_missing", source)
    try:
        payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        source["error"] = str(error)
        return _unavailable("registry_unreadable", source)
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    expectations = jobs.get(job_name) if isinstance(jobs, dict) else None
    if not isinstance(expectations, dict):
        return _unavailable("job_expectations_missing", source)
    line_range = _registry_line_range(registry_path, job_name)
    if line_range is not None:
        source = _source(
            registry_path, line_start=line_range[0], line_end=line_range[1]
        )
    return _available(expectations, source)


def collect_for_fetch_prices(
    *,
    root: Path = ROOT,
    history_path: Path | None = None,
    registry_path: Path | None = None,
) -> Evidence:
    """Collect sourced log, CSV sample, history, and registry evidence."""
    history = history_path or root / "watchman" / "history.jsonl"
    registry = registry_path or root / "watchman" / "registry.yaml"
    csv_head, csv_tail = _sample_lines(
        root / "data" / "prices.csv", first=3, last=5
    )
    trend, history_issues = _fetch_history_trend(history)
    return {
        "job": "fetch_prices",
        "items": {
            "log_tail": _tail_lines(root / "logs" / "fetch_prices.log", 50),
            "csv_first_3_lines": csv_head,
            "csv_last_5_lines": csv_tail,
            "size_and_row_count_trend": trend,
            "registry_expectations": _registry_expectations(
                registry, "fetch_prices"
            ),
            "history_read_issues": history_issues,
        },
    }


def collect_for_backup_db(
    *,
    root: Path = ROOT,
    history_path: Path | None = None,
    registry_path: Path | None = None,
) -> Evidence:
    """Collect sourced log, database, archive, history, and registry evidence."""
    history = history_path or root / "watchman" / "history.jsonl"
    registry = registry_path or root / "watchman" / "registry.yaml"
    database_path = root / "data" / "demo.db"
    archives = [
        path
        for path in (root / "backups").glob("demo_db_*.tar.gz")
        if path.is_file()
    ]
    latest_archive = (
        max(archives, key=lambda path: (path.stat().st_mtime_ns, str(path)))
        if archives
        else None
    )

    if database_path.exists():
        try:
            database = _available(
                {"exists": True, "size_bytes": database_path.stat().st_size},
                _source(database_path),
            )
        except OSError as error:
            source = _source(database_path)
            source["error"] = str(error)
            database = _unavailable("source_db_unreadable", source)
    else:
        database = _unavailable("source_db_missing", _source(database_path))

    if latest_archive is None:
        archive_metadata = _unavailable(
            "archive_missing",
            _source(root / "backups" / "demo_db_*.tar.gz"),
        )
        archive_members = _unavailable(
            "archive_missing",
            _source(root / "backups" / "demo_db_*.tar.gz", detail="members"),
        )
    else:
        archive_metadata = _available(
            {"exists": True, "size_bytes": latest_archive.stat().st_size},
            _source(latest_archive),
        )
        try:
            with tarfile.open(latest_archive, "r:gz") as archive:
                members = archive.getnames()
            archive_members = _available(
                members, _source(latest_archive, detail="members")
            )
        except (OSError, tarfile.TarError) as error:
            source = _source(latest_archive, detail="members")
            source["error"] = str(error)
            archive_members = _unavailable("archive_members_unreadable", source)

    trend, history_issues = _archive_count_trend(history)
    return {
        "job": "backup_db",
        "items": {
            "log_tail": _tail_lines(root / "logs" / "backup_db.log", 50),
            "source_database": database,
            "latest_archive": archive_metadata,
            "latest_archive_members": archive_members,
            "archive_count_trend": trend,
            "registry_expectations": _registry_expectations(registry, "backup_db"),
            "history_read_issues": history_issues,
        },
    }
