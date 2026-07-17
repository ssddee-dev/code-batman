from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchman import investigator

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


class FakeResponses:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.outputs.pop(0))


class FakeClient:
    def __init__(self, outputs: list[str]) -> None:
        self.responses = FakeResponses(outputs)


def inspection(flags: list[dict] | None = None) -> dict:
    source = {
        "path": "/evidence/prices.csv",
        "line_start": 1,
        "line_end": 1,
    }
    return {
        "job": "fetch_prices",
        "flags": flags
        if flags is not None
        else [
            {
                "code": "schema_mismatch",
                "observed": [],
                "reference": ["timestamp", "symbol", "price_usd"],
                "sources": [source],
            }
        ],
        "observed": {
            "schema": {
                "status": "available",
                "value": [],
                "source": source,
            }
        },
    }


def collector(**_: object) -> dict:
    return {
        "job": "fetch_prices",
        "items": {
            "csv_first_3_lines": {
                "status": "available",
                "value": ["", "BTC,60000"],
                "source": {
                    "path": "/evidence/prices.csv",
                    "line_start": 1,
                    "line_end": 2,
                    "source_id": "/evidence/prices.csv:L1-L2",
                },
            }
        },
    }


def valid_dossier(source_id: str = "/evidence/prices.csv:L1-L2") -> dict:
    return {
        "what_was_flagged": [
            {
                "flag_code": "schema_mismatch",
                "details": "Observed schema differs from the registry declaration.",
                "source_ids": [source_id],
            }
        ],
        "what_the_evidence_shows": [
            {
                "finding": "The first sampled CSV line is blank.",
                "source_ids": [source_id],
            }
        ],
        "suspected_areas": [
            {
                "area": "CSV header handling",
                "likelihood": "possible",
                "rationale": "The sample begins with a blank line.",
                "source_ids": [source_id],
            }
        ],
        "not_checked": ["The process that changed the file before this run."],
        "human_decision_needed": {
            "question": "Which bounded action, if any, should be approved?",
            "options": [
                {
                    "action_id": "rerun_only",
                    "description": "Run the price job once more.",
                    "risk_note": "A rerun may append more rows without changing the header.",
                },
                {
                    "action_id": "none",
                    "description": "Take no execution action.",
                    "risk_note": "The observed mismatch remains present.",
                },
            ],
        },
    }


class InvestigatorTests(unittest.TestCase):
    def test_unflagged_inspection_makes_no_api_call(self) -> None:
        client = FakeClient([])
        result = investigator.investigate(
            inspection([]), client=client, collector=collector
        )
        self.assertIsNone(result)
        self.assertEqual(client.responses.calls, [])

    def test_valid_dossier_is_saved_after_one_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "dossiers"
            client = FakeClient([json.dumps(valid_dossier())])

            dossier_path = investigator.investigate(
                inspection(),
                client=client,
                collector=collector,
                dossiers_dir=destination,
                now=NOW,
            )

            self.assertEqual(len(client.responses.calls), 1)
            self.assertEqual(
                dossier_path.name, "fetch_prices_20260717_120000_000000.json"
            )
            saved = json.loads(dossier_path.read_text(encoding="utf-8"))
            self.assertEqual(set(saved), investigator.TOP_LEVEL_SECTIONS)
            self.assertEqual(
                client.responses.calls[0]["model"], "gpt-5.6"
            )

    def test_unknown_citation_retries_once_then_accepts_correction(self) -> None:
        invalid = valid_dossier("/invented/source")
        client = FakeClient(
            [json.dumps(invalid), json.dumps(valid_dossier())]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            dossier_path = investigator.investigate(
                inspection(),
                client=client,
                collector=collector,
                dossiers_dir=Path(temp_dir),
                now=NOW,
            )

        self.assertIsNotNone(dossier_path)
        self.assertEqual(len(client.responses.calls), 2)
        retry_input = client.responses.calls[1]["input"][1]["content"]
        self.assertIn("unknown source_id", retry_input)

    def test_two_invalid_outputs_are_preserved_and_fail_explicitly(self) -> None:
        client = FakeClient(["not json", json.dumps(valid_dossier("/unknown"))])
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "dossiers"
            with self.assertRaisesRegex(
                investigator.DossierValidationError, "after two attempts"
            ):
                investigator.investigate(
                    inspection(),
                    client=client,
                    collector=collector,
                    dossiers_dir=destination,
                    now=NOW,
                )

            failed_path = (
                destination
                / "failed"
                / "fetch_prices_20260717_120000_000000.json"
            )
            preserved = json.loads(failed_path.read_text(encoding="utf-8"))

        self.assertEqual(len(client.responses.calls), 2)
        self.assertEqual(preserved["raw_outputs"][0], "not json")
        self.assertEqual(len(preserved["raw_outputs"]), 2)

    def test_validator_rejects_invalid_action_and_extra_section(self) -> None:
        evidence_package = investigator.build_evidence_package(
            inspection(), collector=collector
        )
        dossier = valid_dossier()
        dossier["human_decision_needed"]["options"][0]["action_id"] = "restart"
        dossier["verdict"] = "broken"

        errors = investigator.validate_dossier(dossier, evidence_package)

        self.assertTrue(any("unexpected top-level" in error for error in errors))
        self.assertTrue(any("action_id is invalid" in error for error in errors))

    def test_notify_cli_sends_each_saved_dossier(self) -> None:
        summaries = [
            {
                "job": "fetch_prices",
                "dossier_path": "/tmp/fetch_prices_dossier.json",
            }
        ]
        with patch.object(
            investigator, "load_latest_inspections", return_value=[]
        ):
            with patch.object(
                investigator, "investigate_flagged", return_value=summaries
            ):
                with patch(
                    "watchman.notifier.send_dossier"
                ) as send_dossier:
                    with patch("builtins.print"):
                        exit_code = investigator.main(["--notify"])

        self.assertEqual(exit_code, 0)
        send_dossier.assert_called_once_with(
            Path("/tmp/fetch_prices_dossier.json")
        )
        self.assertTrue(summaries[0]["telegram_notified"])


if __name__ == "__main__":
    unittest.main()
