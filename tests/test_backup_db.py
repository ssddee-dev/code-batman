from __future__ import annotations

import sqlite3
import sys
import tarfile
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples import backup_db


class BackupDbTests(unittest.TestCase):
    def test_database_is_seeded_only_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "demo.db"

            self.assertTrue(backup_db.ensure_demo_database(database_path))
            self.assertFalse(backup_db.ensure_demo_database(database_path))

            with closing(sqlite3.connect(database_path)) as connection:
                count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            self.assertEqual(count, 3)

    def test_backup_contains_database_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "data" / "demo.db"
            backup_dir = root / "backups"
            backup_db.ensure_demo_database(database_path)

            archive_path = backup_db.create_backup(
                datetime(2026, 7, 17, 1, 2, 3, tzinfo=timezone.utc),
                database_path,
                backup_dir,
            )

            self.assertEqual(archive_path.name, "demo_db_20260717_010203.tar.gz")
            with tarfile.open(archive_path, "r:gz") as archive:
                self.assertEqual(archive.getnames(), ["demo.db"])


if __name__ == "__main__":
    unittest.main()
