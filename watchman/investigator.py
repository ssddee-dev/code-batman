"""LLM investigation over deterministic evidence for flagged demo jobs only."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from openai import OpenAI

from watchman.collectors import collect_for_backup_db, collect_for_fetch_prices

ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / "watchman" / "history.jsonl"
DOSSIERS_DIR = ROOT / "dossiers"
MODEL = "gpt-5.6"
ACTION_IDS = {"quarantine_and_rerun", "rerun_only", "none"}
TOP_LEVEL_SECTIONS = {
    "what_was_flagged",
    "what_the_evidence_shows",
    "suspected_areas",
    "not_checked",
    "human_decision_needed",
}

Evidence = dict[str, Any]
Collector = Callable[..., Evidence]

COLLECTORS: dict[str, Collector] = {
    "fetch_prices": collect_for_fetch_prices,
    "backup_db": collect_for_backup_db,
}

DOSSIER_SCHEMA: Evidence = {
    "type": "object",
    "additionalProperties": False,
    "required": sorted(TOP_LEVEL_SECTIONS),
    "properties": {
        "what_was_flagged": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["flag_code", "details", "source_ids"],
                "properties": {
                    "flag_code": {"type": "string"},
                    "details": {"type": "string"},
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                },
            },
        },
        "what_the_evidence_shows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["finding", "source_ids"],
                "properties": {
                    "finding": {"type": "string"},
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                },
            },
        },
        "suspected_areas": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["area", "likelihood", "rationale", "source_ids"],
                "properties": {
                    "area": {"type": "string"},
                    "likelihood": {
                        "type": "string",
                        "enum": ["possible", "plausible", "uncertain"],
                    },
                    "rationale": {"type": "string"},
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                },
            },
        },
        "not_checked": {
            "type": "array",
            "items": {"type": "string"},
        },
        "human_decision_needed": {
            "type": "object",
            "additionalProperties": False,
            "required": ["question", "options"],
            "properties": {
                "question": {"type": "string"},
                "options": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["action_id", "description", "risk_note"],
                        "properties": {
                            "action_id": {
                                "type": "string",
                                "enum": sorted(ACTION_IDS),
                            },
                            "description": {"type": "string"},
                            "risk_note": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}

SYSTEM_PROMPT = """\
You create evidence dossiers for Night Watchman. Return only the requested JSON.

Non-negotiable rules:
- Present evidence, never a verdict, fix, or automatic execution.
- Use probabilistic language only in suspected_areas.
- Do not invent missing observations; list investigation gaps in not_checked.
- Every item in what_was_flagged, what_the_evidence_shows, and suspected_areas
  must cite one or more source_ids copied exactly from the evidence package.
- Ask a human what to do. Offer 1-3 options using only these action_id values:
  quarantine_and_rerun, rerun_only, none.
- Do not claim that any action has been approved or executed.
"""


class DossierValidationError(RuntimeError):
    """Raised after both model outputs fail dossier or citation validation."""


def _pointer_source_id(pointer: Evidence) -> str | None:
    path = pointer.get("path")
    if not isinstance(path, str):
        return None
    source_id = path
    line_start = pointer.get("line_start", pointer.get("line"))
    line_end = pointer.get("line_end", line_start)
    if isinstance(line_start, int):
        source_id = f"{source_id}:L{line_start}-L{line_end}"
    detail = pointer.get("detail")
    if isinstance(detail, str):
        source_id = f"{source_id}#{detail}"
    return source_id


def _attach_source_ids(value: Any) -> Any:
    if isinstance(value, dict):
        normalized = {
            key: _attach_source_ids(item) for key, item in value.items()
        }
        if "path" in normalized and "source_id" not in normalized:
            source_id = _pointer_source_id(normalized)
            if source_id is not None:
                normalized["source_id"] = source_id
        return normalized
    if isinstance(value, list):
        return [_attach_source_ids(item) for item in value]
    return value


def build_evidence_package(
    inspection_result: Evidence,
    *,
    root: Path = ROOT,
    collector: Collector | None = None,
) -> Evidence:
    """Combine a flagged inspection with its deterministic collector evidence."""
    job_name = inspection_result.get("job")
    selected_collector = collector or COLLECTORS.get(str(job_name))
    if selected_collector is None:
        raise ValueError(f"no collector declared for job: {job_name}")
    collected = selected_collector(root=root)
    return _attach_source_ids(
        {
            "inspection": copy.deepcopy(inspection_result),
            "collected_evidence": collected,
        }
    )


def source_ids_in(evidence_package: Evidence) -> set[str]:
    """Return every exact source ID available for dossier citations."""
    source_ids: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            source_id = value.get("source_id")
            if isinstance(source_id, str):
                source_ids.add(source_id)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(evidence_package)
    return source_ids


def _validate_cited_items(
    dossier: Evidence,
    section: str,
    available_source_ids: set[str],
    errors: list[str],
) -> None:
    items = dossier.get(section)
    if not isinstance(items, list):
        errors.append(f"{section} must be an array")
        return
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{section}[{index}] must be an object")
            continue
        cited = item.get("source_ids")
        if not isinstance(cited, list) or not cited:
            errors.append(f"{section}[{index}].source_ids must be non-empty")
            continue
        for source_id in cited:
            if not isinstance(source_id, str):
                errors.append(f"{section}[{index}] contains a non-string source_id")
            elif source_id not in available_source_ids:
                errors.append(
                    f"{section}[{index}] cites unknown source_id: {source_id}"
                )


def validate_dossier(
    dossier: Any, evidence_package: Evidence
) -> list[str]:
    """Return explicit schema and citation errors for a candidate dossier."""
    errors: list[str] = []
    if not isinstance(dossier, dict):
        return ["dossier must be a JSON object"]
    actual_sections = set(dossier)
    if actual_sections != TOP_LEVEL_SECTIONS:
        missing = sorted(TOP_LEVEL_SECTIONS - actual_sections)
        extra = sorted(actual_sections - TOP_LEVEL_SECTIONS)
        if missing:
            errors.append(f"missing top-level sections: {missing}")
        if extra:
            errors.append(f"unexpected top-level sections: {extra}")

    available_source_ids = source_ids_in(evidence_package)
    for section in (
        "what_was_flagged",
        "what_the_evidence_shows",
        "suspected_areas",
    ):
        _validate_cited_items(
            dossier, section, available_source_ids, errors
        )

    flagged = dossier.get("what_was_flagged")
    if isinstance(flagged, list):
        for index, item in enumerate(flagged):
            if not isinstance(item, dict):
                continue
            if set(item) != {"flag_code", "details", "source_ids"}:
                errors.append(f"what_was_flagged[{index}] has invalid fields")
            if not isinstance(item.get("flag_code"), str) or not isinstance(
                item.get("details"), str
            ):
                errors.append(
                    f"what_was_flagged[{index}] text fields must be strings"
                )

    findings = dossier.get("what_the_evidence_shows")
    if isinstance(findings, list):
        for index, item in enumerate(findings):
            if not isinstance(item, dict):
                continue
            if set(item) != {"finding", "source_ids"}:
                errors.append(
                    f"what_the_evidence_shows[{index}] has invalid fields"
                )
            if not isinstance(item.get("finding"), str):
                errors.append(
                    f"what_the_evidence_shows[{index}].finding must be a string"
                )

    suspected = dossier.get("suspected_areas")
    if isinstance(suspected, list):
        for index, item in enumerate(suspected):
            if not isinstance(item, dict):
                continue
            if set(item) != {"area", "likelihood", "rationale", "source_ids"}:
                errors.append(f"suspected_areas[{index}] has invalid fields")
            if item.get("likelihood") not in {"possible", "plausible", "uncertain"}:
                errors.append(
                    f"suspected_areas[{index}].likelihood is invalid"
                )
            if not isinstance(item.get("area"), str) or not isinstance(
                item.get("rationale"), str
            ):
                errors.append(
                    f"suspected_areas[{index}] text fields must be strings"
                )

    not_checked = dossier.get("not_checked")
    if not isinstance(not_checked, list) or not all(
        isinstance(item, str) for item in not_checked
    ):
        errors.append("not_checked must be an array of strings")

    decision = dossier.get("human_decision_needed")
    if not isinstance(decision, dict):
        errors.append("human_decision_needed must be an object")
    else:
        if set(decision) != {"question", "options"}:
            errors.append("human_decision_needed has invalid fields")
        if not isinstance(decision.get("question"), str):
            errors.append("human_decision_needed.question must be a string")
        options = decision.get("options")
        if not isinstance(options, list) or not 1 <= len(options) <= 3:
            errors.append("human_decision_needed.options must contain 1-3 items")
        else:
            for index, option in enumerate(options):
                if not isinstance(option, dict):
                    errors.append(
                        f"human_decision_needed.options[{index}] must be an object"
                    )
                    continue
                if set(option) != {"action_id", "description", "risk_note"}:
                    errors.append(
                        f"human_decision_needed.options[{index}] has invalid fields"
                    )
                if option.get("action_id") not in ACTION_IDS:
                    errors.append(
                        f"human_decision_needed.options[{index}].action_id is invalid"
                    )
                if not isinstance(option.get("description"), str) or not isinstance(
                    option.get("risk_note"), str
                ):
                    errors.append(
                        f"human_decision_needed.options[{index}] text fields "
                        "must be strings"
                    )
    return errors


def _model_input(evidence_package: Evidence, correction: str | None = None) -> list:
    user_content = (
        "Create an evidence dossier from this package:\n"
        + json.dumps(evidence_package, sort_keys=True)
    )
    if correction is not None:
        user_content += (
            "\n\nThe prior output failed local validation. Correct these errors "
            "without adding unsupported claims:\n"
            + correction
        )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _call_model(
    client: Any, evidence_package: Evidence, correction: str | None = None
) -> str:
    response = client.responses.create(
        model=MODEL,
        input=_model_input(evidence_package, correction),
        text={
            "format": {
                "type": "json_schema",
                "name": "night_watchman_evidence_dossier",
                "strict": True,
                "schema": DOSSIER_SCHEMA,
            }
        },
    )
    output_text = getattr(response, "output_text", None)
    if not isinstance(output_text, str):
        return ""
    return output_text


def _parse_and_validate(
    raw_output: str, evidence_package: Evidence
) -> tuple[Evidence | None, list[str]]:
    try:
        dossier = json.loads(raw_output)
    except json.JSONDecodeError as error:
        return None, [f"model output is not valid JSON: {error}"]
    errors = validate_dossier(dossier, evidence_package)
    return dossier if isinstance(dossier, dict) else None, errors


def _timestamp(now: datetime | None = None) -> str:
    value = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return value.strftime("%Y%m%d_%H%M%S_%f")


def _preserve_failed_outputs(
    job_name: str,
    raw_outputs: list[str],
    validation_errors: list[list[str]],
    *,
    dossiers_dir: Path,
    now: datetime | None = None,
) -> Path:
    failed_dir = dossiers_dir / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    failed_path = failed_dir / f"{job_name}_{_timestamp(now)}.json"
    failed_path.write_text(
        json.dumps(
            {
                "job": job_name,
                "model": MODEL,
                "raw_outputs": raw_outputs,
                "validation_errors": validation_errors,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return failed_path


def investigate(
    inspection_result: Evidence,
    *,
    root: Path = ROOT,
    dossiers_dir: Path | None = None,
    client: Any | None = None,
    collector: Collector | None = None,
    now: datetime | None = None,
) -> Path | None:
    """Investigate one flagged result and save a validated evidence dossier."""
    flags = inspection_result.get("flags")
    if not isinstance(flags, list) or not flags:
        return None

    job_name = inspection_result.get("job")
    if not isinstance(job_name, str):
        raise ValueError("inspection result must contain a string job name")
    evidence_package = build_evidence_package(
        inspection_result, root=root, collector=collector
    )
    destination = dossiers_dir or root / "dossiers"

    if client is None:
        load_dotenv(dotenv_path=root / ".env")
        client = OpenAI()

    raw_outputs: list[str] = []
    validation_errors: list[list[str]] = []
    correction: str | None = None
    for _attempt in range(2):
        raw_output = _call_model(client, evidence_package, correction)
        raw_outputs.append(raw_output)
        dossier, errors = _parse_and_validate(raw_output, evidence_package)
        validation_errors.append(errors)
        if not errors and dossier is not None:
            destination.mkdir(parents=True, exist_ok=True)
            dossier_path = destination / f"{job_name}_{_timestamp(now)}.json"
            dossier_path.write_text(
                json.dumps(dossier, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return dossier_path
        correction = json.dumps(errors)

    failed_path = _preserve_failed_outputs(
        job_name,
        raw_outputs,
        validation_errors,
        dossiers_dir=destination,
        now=now,
    )
    raise DossierValidationError(
        f"dossier validation failed after two attempts; raw outputs: {failed_path}"
    )


def load_latest_inspections(
    history_path: Path = HISTORY_PATH,
) -> list[Evidence]:
    """Return the latest persisted inspection for each scoped demo job."""
    latest: dict[str, Evidence] = {}
    if not history_path.exists():
        return []
    with history_path.open(encoding="utf-8") as history_file:
        for line in history_file:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("job") in COLLECTORS:
                latest[record["job"]] = record
    return [latest[job] for job in COLLECTORS if job in latest]


def investigate_flagged(
    inspections: list[Evidence],
    *,
    root: Path = ROOT,
    client: Any | None = None,
) -> list[Evidence]:
    """Investigate flagged inputs only and return short saved-dossier summaries."""
    summaries: list[Evidence] = []
    shared_client = client
    for inspection in inspections:
        flags = inspection.get("flags")
        if not isinstance(flags, list) or not flags:
            continue
        if shared_client is None:
            load_dotenv(dotenv_path=root / ".env")
            shared_client = OpenAI()
        dossier_path = investigate(
            inspection, root=root, client=shared_client
        )
        assert dossier_path is not None
        dossier = json.loads(dossier_path.read_text(encoding="utf-8"))
        summaries.append(
            {
                "job": inspection["job"],
                "flag_count": len(flags),
                "dossier_path": str(dossier_path),
                "finding_count": len(dossier["what_the_evidence_shows"]),
                "action_options": [
                    option["action_id"]
                    for option in dossier["human_decision_needed"]["options"]
                ],
            }
        )
    return summaries


def main() -> int:
    """Investigate the latest flagged inspections and print short summaries."""
    summaries = investigate_flagged(load_latest_inspections())
    print(json.dumps({"dossiers": summaries}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
