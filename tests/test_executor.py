from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchman import executor

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def write_job(
    root: Path,
    job: str,
    output: str,
    exit_code: int = 0,
) -> None:
    script = root / "examples" / f"{job}.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    registry = root / "watchman" / "registry.yaml"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "name": job,
                        "command": [sys.executable, str(script.relative_to(root))],
                        "output": output,
                        "expectations": {"min_size_bytes": 1},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


class ExecutorTests(unittest.TestCase):
    def test_quarantine_and_rerun_moves_fetch_output_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "data" / "prices.csv"
            output.parent.mkdir(parents=True)
            output.write_text("timestamp,symbol,price_usd\n", encoding="utf-8")
            write_job(root, "fetch_prices", "data/prices.csv")

            record = executor.quarantine_and_rerun(
                "fetch_prices",
                root=root,
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
            write_job(root, "backup_db", "backups/demo_db_*.tar.gz")

            record = executor.quarantine_and_rerun(
                "backup_db",
                root=root,
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
            write_job(root, "fetch_prices", "data/prices.csv", exit_code=7)

            record = executor.rerun_only(
                "fetch_prices",
                root=root,
                now=NOW,
            )

            self.assertTrue(output.exists())
            self.assertEqual(record["files_moved"]["items"], [])
            self.assertEqual(record["job_exit_status"]["exit_code"], 7)

    def test_missing_output_is_explicit_and_job_still_reruns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_job(root, "fetch_prices", "data/prices.csv")

            record = executor.quarantine_and_rerun(
                "fetch_prices",
                root=root,
                now=NOW,
            )

            self.assertEqual(record["files_moved"]["status"], "unavailable")
            self.assertEqual(
                record["files_moved"]["reason"], "current_output_missing"
            )
            self.assertEqual(record["job_exit_status"]["exit_code"], 0)

    def test_failed_quarantine_does_not_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "data" / "prices.csv"
            output.parent.mkdir(parents=True)
            output.write_text("evidence", encoding="utf-8")
            write_job(root, "fetch_prices", "data/prices.csv")

            with patch(
                "watchman.executor.shutil.move",
                side_effect=OSError("move unavailable"),
            ):
                with patch("watchman.executor.subprocess.run") as run:
                    record = executor.quarantine_and_rerun(
                        "fetch_prices",
                        root=root,
                        now=NOW,
                    )

            self.assertEqual(
                record["files_moved"]["reason"], "quarantine_move_failed"
            )
            self.assertEqual(
                record["job_exit_status"]["reason"],
                "rerun_not_attempted_after_quarantine_failure",
            )
            run.assert_not_called()
            self.assertTrue(output.exists())

    def test_synthetic_third_job_uses_its_declared_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_job(
                root,
                "synthetic_third_job",
                "artifacts/third.jsonl",
                exit_code=3,
            )

            record = executor.rerun_only(
                "synthetic_third_job",
                root=root,
                now=NOW,
            )

        self.assertEqual(record["job"], "synthetic_third_job")
        self.assertEqual(record["job_exit_status"]["exit_code"], 3)
        self.assertEqual(
            record["job_exit_status"]["command"][-1],
            "examples/synthetic_third_job.py",
        )

    def test_run_all_registered_uses_registry_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            examples = root / "examples"
            examples.mkdir()
            registry = root / "watchman" / "registry.yaml"
            registry.parent.mkdir()
            declarations = []
            for name, exit_code in (
                ("first", 0),
                ("synthetic_third_job", 4),
            ):
                script = examples / f"{name}.py"
                script.write_text(
                    f"raise SystemExit({exit_code})\n",
                    encoding="utf-8",
                )
                declarations.append(
                    {
                        "name": name,
                        "command": [
                            sys.executable,
                            str(script.relative_to(root)),
                        ],
                        "output": f"artifacts/{name}.txt",
                        "expectations": {"min_size_bytes": 1},
                    }
                )
            registry.write_text(
                json.dumps({"jobs": declarations}),
                encoding="utf-8",
            )

            records = executor.run_all_registered(root=root)

        self.assertEqual(
            [record["job"] for record in records],
            ["first", "synthetic_third_job"],
        )
        self.assertEqual(
            [
                record["job_exit_status"]["exit_code"]
                for record in records
            ],
            [0, 4],
        )

    def test_dispatch_rejects_unscoped_actions_and_unregistered_jobs(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported action"):
            executor.execute("restart", "fetch_prices")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_job(root, "registered_job", "data/output.txt")
            with self.assertRaisesRegex(
                ValueError, "job is not declared in registry"
            ):
                executor.execute(
                    "rerun_only",
                    "unregistered_job",
                    root=root,
                )


if __name__ == "__main__":
    unittest.main()
