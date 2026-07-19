from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples import fetch_prices


class FetchPricesTests(unittest.TestCase):
    @staticmethod
    def payload() -> dict[str, dict[str, float]]:
        return {
            "bitcoin": {"usd": 61_234.5},
            "ethereum": {"usd": 3_456.7},
        }

    def test_append_prices_writes_expected_schema_and_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "prices.csv"

            with patch.object(fetch_prices, "OUTPUT_PATH", output_path):
                written = fetch_prices.append_prices(
                    self.payload(), datetime(2026, 7, 17, tzinfo=timezone.utc)
                )

            with output_path.open(newline="", encoding="utf-8") as output:
                rows = list(csv.DictReader(output))

            self.assertEqual(written, 2)
            self.assertEqual(
                tuple(rows[0].keys()), ("timestamp", "symbol", "price_usd")
            )
            self.assertEqual([row["symbol"] for row in rows], ["BTC", "ETH"])

    def test_append_prices_writes_header_to_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "prices.csv"
            output_path.touch()

            with patch.object(fetch_prices, "OUTPUT_PATH", output_path):
                fetch_prices.append_prices(
                    self.payload(), datetime(2026, 7, 17, tzinfo=timezone.utc)
                )

            with output_path.open(newline="", encoding="utf-8") as output:
                rows = list(csv.reader(output))

            self.assertEqual(rows[0], list(fetch_prices.CSV_SCHEMA))
            self.assertEqual(len(rows), 3)

    def test_append_prices_does_not_rewrite_nonempty_malformed_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "prices.csv"
            output_path.write_text("\n", encoding="utf-8")

            with patch.object(fetch_prices, "OUTPUT_PATH", output_path):
                fetch_prices.append_prices(
                    self.payload(), datetime(2026, 7, 17, tzinfo=timezone.utc)
                )

            with output_path.open(newline="", encoding="utf-8") as output:
                rows = list(csv.reader(output))

            self.assertEqual(rows[0], [])
            self.assertEqual([row[1] for row in rows[1:]], ["BTC", "ETH"])

    def test_append_prices_rejects_missing_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                fetch_prices, "OUTPUT_PATH", Path(temp_dir) / "prices.csv"
            ):
                with self.assertRaisesRegex(ValueError, "ethereum"):
                    fetch_prices.append_prices(
                        {"bitcoin": {"usd": 61_234.5}},
                        datetime(2026, 7, 17, tzinfo=timezone.utc),
                    )


if __name__ == "__main__":
    unittest.main()
