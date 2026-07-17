from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchman import executor

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def write_job(root: Path, job: str, exit_code: int = 0) -> None:
    script = root / "jobs" / f"{job}.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )


class ExecutorTests(unittest.TestCase):
    def test_quarantine_and_rerun_moves_fetch_output_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "data" / "prices.csv"
            output.parent.mkdir(parents=True)
            output.write_text("timestamp,symbol,price_usd\n", encoding="utf-8")
            write_job(root, "fetch_prices")

            record = executor.quarantine_and_rerun(
                "fetch_prices",
                root=root,
                python_executable=sys.executable,
                now=NOW,
            )

            moved = record["files_moved"]["items"][0]
            destination = Path(moved["to"])
            self.assertFalse(output.exists())
            self.assertTrue(destination.exists())
            self.assertEqual(destination.read_text(encoding="utf-8"), "timestamp,symbol,price_usd\n")
            self.assertEqual(
                destination.name,
                "prices.csv.20260717_120000_000000",
            )
            self.assertEqual(record["job_exit_status"]["exit_code"], 0)

    def test_backup_quarantine_moves_only_latest_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backups = root / "backups"
            backups.mkdir()
            older = backups / "demo_db_20260717_000001.tar.gz"
            latest = backups / "demo_db_20260717_000002.tar.gz"
            older.write_bytes(b"older")
            latest.write_bytes(b"latest")
            os.utime(older, (1, 1))
            os.utime(latest, (2, 2))
            write_job(root, "backup_db")

            record = executor.quarantine_and_rerun(
                "backup_db",
                root=root,
                python_executable=sys.executable,
                now=NOW,
            )

            self.assertTrue(older.exists())
            self.assertFalse(latest.exists())
            moved = record["files_moved"]["items"][0]
            self.assertEqual(moved["from"], str(latest))
            self.assertTrue(Path(moved["to"]).exists())

    def test_rerun_only_preserves_output_and_reports_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "data" / "prices.csv"
            output.parent.mkdir(parents=True)
            output.write_text("evidence", encoding="utf-8")
            write_job(root, "fetch_prices", exit_code=7)

            record = executor.rerun_only(
                "fetch_prices",
                root=root,
                python_executable=sys.executable,
                now=NOW,
            )

            self.assertTrue(output.exists())
            self.assertEqual(record["files_moved"]["items"], [])
            self.assertEqual(record["job_exit_status"]["exit_code"], 7)

    def test_missing_output_is_explicit_and_job_still_reruns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_job(root, "fetch_prices")

            record = executor.quarantine_and_rerun(
                "fetch_prices",
                root=root,
                python_executable=sys.executable,
                now=NOW,
            )

            self.assertEqual(record["files_moved"]["status"], "unavailable")
            self.assertEqual(
                record["files_moved"]["reason"], "current_output_missing"
            )
            self.assertEqual(record["job_exit_status"]["exit_code"], 0)

    def test_dispatch_rejects_unscoped_actions_and_jobs(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported action"):
            executor.execute("restart", "fetch_prices")
        with self.assertRaisesRegex(ValueError, "unsupported job"):
            executor.execute("rerun_only", "unregistered_job")


if __name__ == "__main__":
    unittest.main()
