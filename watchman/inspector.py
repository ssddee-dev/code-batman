#!/usr/bin/env python3
"""Deterministically inspect demo job artifacts and emit sourced evidence."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "watchman" / "registry.yaml"
HISTORY_PATH = ROOT / "watchman" / "history.jsonl"

Evidence = dict[str, Any]


def unavailable(reason: str, source: dict[str, Any]) -> Evidence:
    """Return an explicit unavailable measurement with its evidence source."""
    return {"status": "unavailable", "reason": reason, "source": source}


def available(value: Any, source: dict[str, Any]) -> Evidence:
    """Return an available raw measurement with its evidence source."""
    return {"status": "available", "value": value, "source": source}


def load_registry(registry_path: Path = REGISTRY_PATH) -> dict[str, Evidence]:
    """Load declared job expectations and identify their registry source."""
    with registry_path.open(encoding="utf-8") as registry_file:
        payload = yaml.safe_load(registry_file)
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), dict):
        raise ValueError(f"registry must contain a jobs mapping: {registry_path}")
    return payload["jobs"]


def load_history(
    history_path: Path = HISTORY_PATH,
) -> tuple[list[tuple[int, Evidence]], list[Evidence]]:
    """Load prior inspections with exact JSONL line pointers and parse issues."""
    records: list[tuple[int, Evidence]] = []
    issues: list[Evidence] = []
    if not history_path.exists():
        return records, [
            unavailable(
                "history_file_missing",
                {"path": str(history_path)},
            )
        ]

    with history_path.open(encoding="utf-8") as history_file:
        for line_number, line in enumerate(history_file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                issues.append(
                    unavailable(
                        "history_line_invalid_json",
                        {
                            "path": str(history_path),
                            "line": line_number,
                            "error": str(error),
                        },
                    )
                )
                continue
            if isinstance(record, dict) and isinstance(record.get("job"), str):
                records.append((line_number, record))
    return records, issues


def latest_prior_for_job(
    job_name: str, history: list[tuple[int, Evidence]]
) -> tuple[int, Evidence] | None:
    """Return the latest prior inspection and its history line for a job."""
    for line_number, record in reversed(history):
        if record.get("job") == job_name:
            return line_number, record
    return None


def latest_output(root: Path, pattern: str) -> Path | None:
    """Return the newest matching artifact by observed modification time."""
    candidates = [path for path in root.glob(pattern) if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, str(path)))


def read_csv_evidence(output_path: Path) -> tuple[Evidence, Evidence]:
    """Return sourced CSV row-count and schema evidence."""
    source = {"path": str(output_path)}
    try:
        with output_path.open(newline="", encoding="utf-8") as output_file:
            reader = csv.reader(output_file)
            header = next(reader, None)
            if header is None:
                return (
                    available(0, source),
                    unavailable("csv_header_missing", source),
                )
            row_count = sum(1 for _ in reader)
    except (OSError, UnicodeError, csv.Error) as error:
        error_source = {"path": str(output_path), "error": str(error)}
        return (
            unavailable("csv_row_count_uncomputable", error_source),
            unavailable("csv_schema_uncomputable", error_source),
        )
    return available(row_count, source), available(header, source)


def add_flag(
    flags: list[Evidence],
    code: str,
    observed: Any,
    reference: Any,
    sources: list[dict[str, Any]],
) -> None:
    """Append a deterministic flag containing raw values and source pointers."""
    flags.append(
        {
            "code": code,
            "observed": observed,
            "reference": reference,
            "sources": sources,
        }
    )


def inspect_job(
    job_name: str,
    expectations: Evidence,
    *,
    root: Path = ROOT,
    registry_path: Path = REGISTRY_PATH,
    history_path: Path = HISTORY_PATH,
    prior: tuple[int, Evidence] | None = None,
    inspected_at: datetime | None = None,
    history_issues: list[Evidence] | None = None,
) -> Evidence:
    """Produce sourced observations and deterministic flags for one demo job."""
    now = (inspected_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    pattern = expectations["output_pattern"]
    pattern_source = {"path": str(root / pattern), "pattern": pattern}
    registry_source = {"path": str(registry_path), "job": job_name}
    output_path = latest_output(root, pattern)
    flags: list[Evidence] = []

    if output_path is None:
        output: Evidence = unavailable("output_not_found", pattern_source)
        observed = {
            metric: unavailable("output_not_found", pattern_source)
            for metric in ("size_bytes", "row_count", "schema", "age_seconds")
        }
        add_flag(flags, "output_missing", "not_found", pattern, [pattern_source])
    else:
        artifact_source = {"path": str(output_path)}
        stat = output_path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        age_seconds = max(0.0, (now - modified_at).total_seconds())
        output = {
            "status": "available",
            "path": str(output_path),
            "pattern": pattern,
            "modified_at": modified_at.isoformat(),
            "source": artifact_source,
        }
        observed = {
            "size_bytes": available(stat.st_size, artifact_source),
            "age_seconds": available(age_seconds, artifact_source),
        }

        if "schema" in expectations or "min_rows" in expectations:
            row_count, schema = read_csv_evidence(output_path)
            observed["row_count"] = row_count
            observed["schema"] = schema
        else:
            observed["row_count"] = unavailable(
                "not_declared_for_job", registry_source
            )
            observed["schema"] = unavailable(
                "not_declared_for_job", registry_source
            )

        minimum_size = expectations.get("min_size_bytes")
        if (
            minimum_size is not None
            and observed["size_bytes"]["value"] < minimum_size
        ):
            add_flag(
                flags,
                "size_below_minimum",
                observed["size_bytes"]["value"],
                minimum_size,
                [artifact_source, registry_source],
            )

        minimum_rows = expectations.get("min_rows")
        if (
            minimum_rows is not None
            and observed["row_count"]["status"] == "available"
            and observed["row_count"]["value"] < minimum_rows
        ):
            add_flag(
                flags,
                "row_count_below_minimum",
                observed["row_count"]["value"],
                minimum_rows,
                [artifact_source, registry_source],
            )

        expected_schema = expectations.get("schema")
        if (
            expected_schema is not None
            and observed["schema"]["status"] == "available"
            and observed["schema"]["value"] != expected_schema
        ):
            add_flag(
                flags,
                "schema_mismatch",
                observed["schema"]["value"],
                expected_schema,
                [artifact_source, registry_source],
            )

        frequency = expectations.get("expected_frequency_seconds")
        if frequency is not None and age_seconds > frequency:
            add_flag(
                flags,
                "output_stale",
                age_seconds,
                frequency,
                [artifact_source, registry_source],
            )

    if prior is None:
        history_reference: Evidence = unavailable(
            "no_prior_inspection_for_job", {"path": str(history_path)}
        )
    else:
        prior_line, prior_record = prior
        prior_source = {"path": str(history_path), "line": prior_line}
        prior_observed = prior_record.get("observed")
        if not isinstance(prior_observed, dict):
            history_reference = unavailable(
                "prior_observations_unavailable", prior_source
            )
        else:
            history_reference = {
                "status": "available",
                "source": prior_source,
                "inspected_at": prior_record.get("inspected_at", "unavailable"),
            }
            comparisons = (
                ("row_count", "row_count_drop"),
                ("schema", "schema_change"),
            )
            for metric, code in comparisons:
                current_metric = observed.get(metric, {})
                prior_metric = prior_observed.get(metric, {})
                if (
                    current_metric.get("status") != "available"
                    or prior_metric.get("status") != "available"
                ):
                    continue
                current_value = current_metric["value"]
                prior_value = prior_metric["value"]
                changed = (
                    current_value < prior_value
                    if metric == "row_count"
                    else current_value != prior_value
                )
                if changed:
                    add_flag(
                        flags,
                        code,
                        current_value,
                        prior_value,
                        [current_metric["source"], prior_source],
                    )

            current_size = observed.get("size_bytes", {})
            prior_size = prior_observed.get("size_bytes", {})
            if (
                current_size.get("status") == "available"
                and prior_size.get("status") == "available"
                and current_size["value"] < prior_size["value"] * 0.5
            ):
                add_flag(
                    flags,
                    "size_drop_over_50_percent",
                    current_size["value"],
                    prior_size["value"],
                    [current_size["source"], prior_source],
                )

    return {
        "job": job_name,
        "inspected_at": now.isoformat(),
        "registry_source": registry_source,
        "expectations": expectations,
        "output": output,
        "observed": observed,
        "history_reference": history_reference,
        "history_read_issues": history_issues or [],
        "flags": flags,
    }


def inspect_all(
    *,
    root: Path = ROOT,
    registry_path: Path = REGISTRY_PATH,
    history_path: Path = HISTORY_PATH,
    inspected_at: datetime | None = None,
) -> list[Evidence]:
    """Inspect both registered jobs and append each sourced result to history."""
    registry = load_registry(registry_path)
    history, history_issues = load_history(history_path)
    results: list[Evidence] = []
    now = inspected_at or datetime.now(timezone.utc)

    for job_name, expectations in registry.items():
        result = inspect_job(
            job_name,
            expectations,
            root=root,
            registry_path=registry_path,
            history_path=history_path,
            prior=latest_prior_for_job(job_name, history),
            inspected_at=now,
            history_issues=history_issues,
        )
        results.append(result)

    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as history_file:
        for result in results:
            history_file.write(json.dumps(result, sort_keys=True) + "\n")
    return results


def main(argv: list[str] | None = None) -> int:
    """Persist structured evidence and print it unless quiet mode is requested."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="append inspection evidence to history without printing it",
    )
    arguments = parser.parse_args(argv)
    results = inspect_all()
    if not arguments.quiet:
        print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
