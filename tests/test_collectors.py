from __future__ import annotations

import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchman import collectors


def write_registry(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
jobs:
  fetch_prices:
    output_pattern: data/prices.csv
    min_rows: 2
    schema:
      - timestamp
      - symbol
      - price_usd
  backup_db:
    output_pattern: backups/demo_db_*.tar.gz
    min_size_bytes: 100
""".lstrip(),
        encoding="utf-8",
    )


def inspection(job: str, index: int, output_path: str | None = None) -> dict:
    output = (
        {
            "status": "available",
            "path": output_path,
            "source": {"path": output_path},
        }
        if output_path
        else {"status": "unavailable", "reason": "output_not_found"}
    )
    return {
        "job": job,
        "inspected_at": f"2026-07-17T00:{index:02d}:00+00:00",
        "output": output,
        "observed": {
            "size_bytes": {
                "status": "available",
                "value": index * 10,
                "source": {"path": output_path or "missing"},
            },
            "row_count": {
                "status": "available",
                "value": index,
                "source": {"path": output_path or "missing"},
            },
        },
    }


class CollectorTests(unittest.TestCase):
    def test_fetch_prices_collects_requested_ranges_and_last_ten_trend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_path = root / "logs" / "fetch_prices.log"
            log_path.parent.mkdir(parents=True)
            log_path.write_text(
                "\n".join(f"log {number}" for number in range(1, 61)),
                encoding="utf-8",
            )
            csv_path = root / "data" / "prices.csv"
            csv_path.parent.mkdir(parents=True)
            csv_path.write_text(
                "\n".join(f"csv {number}" for number in range(1, 11)),
                encoding="utf-8",
            )
            write_registry(root / "watchman" / "registry.yaml")
            history_path = root / "watchman" / "history.jsonl"
            history_path.write_text(
                "\n".join(
                    json.dumps(inspection("fetch_prices", number, str(csv_path)))
                    for number in range(1, 13)
                ),
                encoding="utf-8",
            )

            evidence = collectors.collect_for_fetch_prices(root=root)

        items = evidence["items"]
        self.assertEqual(items["log_tail"]["source"]["line_start"], 11)
        self.assertEqual(items["log_tail"]["source"]["line_end"], 60)
        self.assertEqual(items["csv_first_3_lines"]["value"], ["csv 1", "csv 2", "csv 3"])
        self.assertEqual(items["csv_last_5_lines"]["source"]["line_start"], 6)
        trend = items["size_and_row_count_trend"]["value"]
        self.assertEqual(len(trend), 10)
        self.assertEqual(trend[0]["row_count"]["value"], 3)
        self.assertEqual(trend[-1]["size_bytes"]["value"], 120)
        self.assertEqual(
            items["registry_expectations"]["source"]["line_start"], 2
        )

    def test_fetch_prices_labels_missing_sources_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = collectors.collect_for_fetch_prices(root=Path(temp_dir))

        items = evidence["items"]
        self.assertEqual(items["log_tail"]["status"], "unavailable")
        self.assertEqual(items["csv_first_3_lines"]["reason"], "source_missing")
        self.assertEqual(
            items["size_and_row_count_trend"]["reason"],
            "no_fetch_prices_inspections",
        )
        self.assertEqual(items["registry_expectations"]["reason"], "registry_missing")

    def test_backup_db_collects_database_archive_members_and_count_trend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "data" / "demo.db"
            database_path.parent.mkdir(parents=True)
            database_path.write_bytes(b"sqlite evidence")
            log_path = root / "logs" / "backup_db.log"
            log_path.parent.mkdir(parents=True)
            log_path.write_text("backup complete\n", encoding="utf-8")
            backups = root / "backups"
            backups.mkdir()
            first_archive = backups / "demo_db_20260717_000001.tar.gz"
            latest_archive = backups / "demo_db_20260717_000002.tar.gz"
            for archive_path in (first_archive, latest_archive):
                with tarfile.open(archive_path, "w:gz") as archive:
                    archive.add(database_path, arcname="demo.db")
            os.utime(first_archive, (1, 1))
            os.utime(latest_archive, (2, 2))
            write_registry(root / "watchman" / "registry.yaml")
            history_path = root / "watchman" / "history.jsonl"
            history_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            inspection("backup_db", 1, str(first_archive))
                        ),
                        json.dumps(
                            inspection("backup_db", 2, str(latest_archive))
                        ),
                        json.dumps(
                            inspection("backup_db", 3, str(latest_archive))
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            evidence = collectors.collect_for_backup_db(root=root)

        items = evidence["items"]
        self.assertEqual(items["source_database"]["value"]["size_bytes"], 15)
        self.assertEqual(items["latest_archive"]["source"]["path"], str(latest_archive))
        self.assertEqual(items["latest_archive_members"]["value"], ["demo.db"])
        counts = [
            point["observed_distinct_archive_paths"]
            for point in items["archive_count_trend"]["value"]
        ]
        self.assertEqual(counts, [1, 2, 2])

    def test_backup_db_labels_corrupt_archive_members_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_path = root / "backups" / "demo_db_20260717_000001.tar.gz"
            archive_path.parent.mkdir(parents=True)
            archive_path.write_bytes(b"not a tar archive")

            evidence = collectors.collect_for_backup_db(root=root)

        members = evidence["items"]["latest_archive_members"]
        self.assertEqual(members["status"], "unavailable")
        self.assertEqual(members["reason"], "archive_members_unreadable")
        self.assertEqual(members["source"]["path"], str(archive_path))


if __name__ == "__main__":
    unittest.main()
