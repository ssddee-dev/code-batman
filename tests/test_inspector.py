from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchman import inspector

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def csv_declaration(**overrides: object) -> dict[str, object]:
    expectations: dict[str, object] = {
        "min_rows": 2,
        "min_size_bytes": 1,
        "schema": ["timestamp", "symbol", "price_usd"],
        "expected_frequency_seconds": 3600,
    }
    expectations.update(overrides)
    return {
        "name": "fetch_prices",
        "command": [sys.executable, "examples/fetch_prices.py"],
        "output": "data/prices.csv",
        "expectations": expectations,
    }


def write_csv(path: Path, schema: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(schema)
        writer.writerows(rows)
    timestamp = NOW.timestamp()
    os.utime(path, (timestamp, timestamp))


def prior_record(
    *,
    row_count: int = 3,
    size_bytes: int = 100,
    schema: list[str] | None = None,
) -> tuple[int, dict[str, object]]:
    source = {"path": "/prior/artifact"}
    return (
        7,
        {
            "job": "fetch_prices",
            "inspected_at": "2026-07-17T11:00:00+00:00",
            "observed": {
                "row_count": inspector.available(row_count, source),
                "size_bytes": inspector.available(size_bytes, source),
                "schema": inspector.available(
                    schema or ["timestamp", "symbol", "price_usd"], source
                ),
            },
        },
    )


class InspectorTests(unittest.TestCase):
    def inspect(
        self,
        root: Path,
        declaration: dict[str, object] | None = None,
        prior: tuple[int, dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return inspector.inspect_job(
            "fetch_prices",
            declaration or csv_declaration(),
            root=root,
            registry_path=root / "watchman" / "registry.yaml",
            history_path=root / "watchman" / "history.jsonl",
            prior=prior,
            inspected_at=NOW,
        )

    @staticmethod
    def codes(result: dict[str, object]) -> set[str]:
        return {flag["code"] for flag in result["flags"]}

    def test_missing_output_is_flagged_and_metrics_are_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.inspect(Path(temp_dir))

        self.assertIn("output_missing", self.codes(result))
        self.assertEqual(result["observed"]["size_bytes"]["status"], "unavailable")
        self.assertEqual(result["observed"]["age_seconds"]["status"], "unavailable")

    def test_size_below_minimum_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_csv(root / "data" / "prices.csv", ["symbol"], [])
            result = self.inspect(root, csv_declaration(min_size_bytes=10_000))

        self.assertIn("size_below_minimum", self.codes(result))

    def test_row_count_below_minimum_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_csv(
                root / "data" / "prices.csv",
                ["timestamp", "symbol", "price_usd"],
                [["2026-07-17T12:00:00+00:00", "BTC", "60000"]],
            )
            result = self.inspect(root)

        self.assertIn("row_count_below_minimum", self.codes(result))

    def test_schema_mismatch_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_csv(root / "data" / "prices.csv", ["symbol", "price"], [])
            result = self.inspect(root, csv_declaration(min_rows=0))

        self.assertIn("schema_mismatch", self.codes(result))

    def test_row_count_drop_against_history_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_csv(
                root / "data" / "prices.csv",
                ["timestamp", "symbol", "price_usd"],
                [
                    ["2026-07-17T12:00:00+00:00", "BTC", "60000"],
                    ["2026-07-17T12:00:00+00:00", "ETH", "3000"],
                ],
            )
            result = self.inspect(root, prior=prior_record(row_count=3))

        self.assertIn("row_count_drop", self.codes(result))
        row_flag = next(
            flag for flag in result["flags"] if flag["code"] == "row_count_drop"
        )
        self.assertEqual(row_flag["sources"][1]["line"], 7)

    def test_size_drop_over_50_percent_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_csv(
                root / "data" / "prices.csv",
                ["timestamp", "symbol", "price_usd"],
                [],
            )
            current_size = (root / "data" / "prices.csv").stat().st_size
            result = self.inspect(
                root,
                csv_declaration(min_rows=0),
                prior_record(size_bytes=current_size * 3),
            )

        self.assertIn("size_drop_over_50_percent", self.codes(result))

    def test_schema_change_against_history_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current_schema = ["timestamp", "ticker", "price_usd"]
            write_csv(root / "data" / "prices.csv", current_schema, [])
            result = self.inspect(
                root,
                csv_declaration(min_rows=0, schema=current_schema),
                prior_record(schema=["timestamp", "symbol", "price_usd"]),
            )

        self.assertIn("schema_change", self.codes(result))

    def test_output_older_than_frequency_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "data" / "prices.csv"
            write_csv(
                output,
                ["timestamp", "symbol", "price_usd"],
                [
                    ["2026-07-17T10:00:00+00:00", "BTC", "60000"],
                    ["2026-07-17T10:00:00+00:00", "ETH", "3000"],
                ],
            )
            old_timestamp = NOW.timestamp() - 3601
            os.utime(output, (old_timestamp, old_timestamp))
            result = self.inspect(root)

        self.assertIn("output_stale", self.codes(result))

    def test_inspect_all_appends_one_history_line_per_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            watchman_dir = root / "watchman"
            watchman_dir.mkdir()
            registry_path = watchman_dir / "registry.yaml"
            history_path = watchman_dir / "history.jsonl"
            registry_path.write_text(
                """
jobs:
  - name: first
    command: ["python3", "first.py"]
    output: data/first.txt
    expectations:
      min_size_bytes: 1
      expected_frequency_seconds: 60
  - name: second
    command: ["python3", "second.py"]
    output: data/second.txt
    expectations:
      min_size_bytes: 1
      expected_frequency_seconds: 60
  - name: synthetic_third_job
    command: ["python3", "third.py"]
    output: data/third.jsonl
    expectations:
      min_size_bytes: 1
      min_rows: 1
      expected_frequency_seconds: 60
""".lstrip(),
                encoding="utf-8",
            )
            history_path.touch()

            results = inspector.inspect_all(
                root=root,
                registry_path=registry_path,
                history_path=history_path,
                inspected_at=NOW,
            )
            history_records = [
                json.loads(line)
                for line in history_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(
            [result["job"] for result in results],
            ["first", "second", "synthetic_third_job"],
        )
        self.assertEqual(
            [record["job"] for record in history_records],
            ["first", "second", "synthetic_third_job"],
        )

    def test_quiet_cli_persists_without_printing(self) -> None:
        with patch.object(inspector, "inspect_all", return_value=[{"job": "first"}]):
            with patch("builtins.print") as print_mock:
                exit_code = inspector.main(["--quiet"])

        self.assertEqual(exit_code, 0)
        print_mock.assert_not_called()

    def test_inspect_one_appends_only_requested_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            watchman_dir = root / "watchman"
            watchman_dir.mkdir()
            registry_path = watchman_dir / "registry.yaml"
            history_path = watchman_dir / "history.jsonl"
            registry_path.write_text(
                """
jobs:
  - name: fetch_prices
    command: ["python3", "examples/fetch_prices.py"]
    output: data/prices.csv
    expectations:
      min_rows: 2
      min_size_bytes: 50
      schema:
        - timestamp
        - symbol
        - price_usd
      expected_frequency_seconds: 60
  - name: backup_db
    command: ["python3", "examples/backup_db.py"]
    output: backups/demo_db_*.tar.gz
    expectations:
      min_size_bytes: 100
      expected_frequency_seconds: 60
""".lstrip(),
                encoding="utf-8",
            )
            history_path.touch()

            result = inspector.inspect_one(
                "fetch_prices",
                root=root,
                registry_path=registry_path,
                history_path=history_path,
                inspected_at=NOW,
            )
            records = [
                json.loads(line)
                for line in history_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(result["job"], "fetch_prices")
        self.assertEqual([record["job"] for record in records], ["fetch_prices"])


if __name__ == "__main__":
    unittest.main()
