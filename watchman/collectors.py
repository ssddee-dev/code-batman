"""Deterministic evidence collection for any registered file-artifact job."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from typing import Any

from watchman.inspector import declared_path, latest_output, load_registry

ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / "watchman" / "history.jsonl"
REGISTRY_PATH = ROOT / "watchman" / "registry.yaml"
TEXT_SUFFIXES = {".csv", ".jsonl", ".ndjson", ".log", ".txt"}

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
    if path.suffix.lower() not in TEXT_SUFFIXES:
        source = _source(path)
        return (
            _unavailable("artifact_not_text_sampled", source),
            _unavailable("artifact_not_text_sampled", source.copy()),
        )
    lines, error = _read_lines(path)
    if error is not None:
        return error, error.copy()
    assert lines is not None
    if not lines:
        unavailable = _unavailable("source_empty", _source(path))
        return unavailable, unavailable.copy()
    head_end = min(first, len(lines))
    tail_start = max(1, len(lines) - last + 1)
    return (
        _available(
            lines[:head_end],
            _source(path, line_start=1, line_end=head_end),
        ),
        _available(
            lines[tail_start - 1 :],
            _source(path, line_start=tail_start, line_end=len(lines)),
        ),
    )


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


def _history_trend(
    history_path: Path, job_name: str
) -> tuple[Evidence, list[Evidence]]:
    records, issues = _history_records(history_path)
    selected = [
        (line_number, record)
        for line_number, record in records
        if record.get("job") == job_name
    ][-10:]
    if not selected:
        return (
            _unavailable(
                "no_inspections_for_job",
                _source(history_path, detail=f"job={job_name}"),
            ),
            issues,
        )

    distinct_paths: set[str] = set()
    points: list[Evidence] = []
    for line_number, record in selected:
        output = record.get("output")
        if isinstance(output, dict) and output.get("status") == "available":
            output_path = output.get("path")
            if isinstance(output_path, str):
                distinct_paths.add(output_path)
        points.append(
            {
                "inspected_at": record.get("inspected_at", "unavailable"),
                "size_bytes": _metric_from_record(
                    record, "size_bytes", history_path, line_number
                ),
                "row_count": _metric_from_record(
                    record, "row_count", history_path, line_number
                ),
                "observed_distinct_output_paths": len(distinct_paths),
                "source": _source(
                    history_path, line_start=line_number, line_end=line_number
                ),
            }
        )
    return (
        _available(
            points,
            _source(
                history_path,
                line_start=selected[0][0],
                line_end=selected[-1][0],
                detail=f"last_10_inspections_for_{job_name}",
            ),
        ),
        issues,
    )


def _registry_line_range(
    registry_path: Path, job_name: str
) -> tuple[int, int] | None:
    lines, error = _read_lines(registry_path)
    if error is not None or lines is None:
        return None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("- name:"):
            continue
        raw_name = stripped.removeprefix("- name:").strip().strip("'\"")
        if raw_name != job_name:
            continue
        indentation = len(line) - len(line.lstrip())
        end = len(lines)
        for candidate in range(index + 1, len(lines)):
            candidate_line = lines[candidate]
            if (
                candidate_line.strip().startswith("- name:")
                and len(candidate_line) - len(candidate_line.lstrip())
                == indentation
            ):
                end = candidate
                break
        return index + 1, end
    return None


def _registry_declaration(
    registry_path: Path, job_name: str, declaration: Evidence
) -> Evidence:
    line_range = _registry_line_range(registry_path, job_name)
    source = (
        _source(
            registry_path,
            line_start=line_range[0],
            line_end=line_range[1],
        )
        if line_range is not None
        else _source(registry_path, detail=f"job={job_name}")
    )
    return _available(declaration, source)


def _artifact_metadata(path: Path | None, pattern_path: Path) -> Evidence:
    if path is None:
        return _unavailable("output_not_found", _source(pattern_path))
    try:
        stat = path.stat()
    except OSError as error:
        source = _source(path)
        source["error"] = str(error)
        return _unavailable("artifact_metadata_unreadable", source)
    return _available(
        {"exists": True, "size_bytes": stat.st_size},
        _source(path),
    )


def _archive_members(path: Path | None, pattern_path: Path) -> Evidence:
    source_path = path or pattern_path
    if path is None:
        return _unavailable(
            "output_not_found", _source(source_path, detail="archive_members")
        )
    try:
        if not tarfile.is_tarfile(path):
            return _unavailable(
                "artifact_not_tar_archive",
                _source(path, detail="archive_members"),
            )
        with tarfile.open(path, "r:*") as archive:
            members = archive.getnames()
    except (OSError, tarfile.TarError) as error:
        source = _source(path, detail="archive_members")
        source["error"] = str(error)
        return _unavailable("archive_members_unreadable", source)
    return _available(members, _source(path, detail="archive_members"))


def collect_for_job(
    job_name: str,
    *,
    root: Path = ROOT,
    history_path: Path | None = None,
    registry_path: Path | None = None,
) -> Evidence:
    """Collect one generic, fully sourced file-artifact evidence package."""
    history = history_path or root / "watchman" / "history.jsonl"
    registry = registry_path or root / "watchman" / "registry.yaml"
    declaration = load_registry(registry).get(job_name)
    if declaration is None:
        raise ValueError(f"job is not declared in registry: {job_name}")

    pattern = declaration["output"]
    pattern_path = declared_path(root, pattern)
    artifact = latest_output(root, pattern)
    if artifact is None:
        head = _unavailable("output_not_found", _source(pattern_path))
        tail = _unavailable("output_not_found", _source(pattern_path))
    else:
        head, tail = _sample_lines(artifact, first=3, last=5)

    log_path = declaration.get("log_path")
    log_tail = (
        _tail_lines(declared_path(root, log_path), 50)
        if isinstance(log_path, str)
        else _unavailable(
            "not_declared_for_job",
            _source(registry, detail=f"job={job_name}.log_path"),
        )
    )
    trend, history_issues = _history_trend(history, job_name)
    return {
        "job": job_name,
        "items": {
            "log_tail": log_tail,
            "artifact_metadata": _artifact_metadata(artifact, pattern_path),
            "artifact_first_3_lines": head,
            "artifact_last_5_lines": tail,
            "archive_members": _archive_members(artifact, pattern_path),
            "size_row_and_output_count_trend": trend,
            "registry_declaration": _registry_declaration(
                registry, job_name, declaration
            ),
            "history_read_issues": _available(
                history_issues,
                _source(history, detail="history_read_issues"),
            ),
        },
    }
