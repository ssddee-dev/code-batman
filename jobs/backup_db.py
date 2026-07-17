#!/usr/bin/env python3
"""Create the demo SQLite database when needed and archive it."""

from __future__ import annotations

import logging
import sqlite3
import tarfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "data" / "demo.db"
BACKUP_DIR = ROOT / "backups"
LOG_PATH = ROOT / "logs" / "backup_db.log"


def configure_logging() -> None:
    """Configure a file log that records source and archive evidence."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def ensure_demo_database(database_path: Path = DATABASE_PATH) -> bool:
    """Ensure seed data exists and return whether the database file was absent."""
    was_absent = not database_path.exists()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        row_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if row_count == 0:
            connection.executemany(
                "INSERT INTO events (name, created_at) VALUES (?, ?)",
                [
                    ("demo_started", "2026-07-17T00:00:00+00:00"),
                    ("first_check", "2026-07-17T00:05:00+00:00"),
                    ("evidence_recorded", "2026-07-17T00:10:00+00:00"),
                ],
            )
        connection.commit()
    return was_absent


def create_backup(
    created_at: datetime | None = None,
    database_path: Path = DATABASE_PATH,
    backup_dir: Path = BACKUP_DIR,
) -> Path:
    """Create a tar.gz artifact and return its evidence file path."""
    timestamp = created_at or datetime.now(timezone.utc)
    backup_dir.mkdir(parents=True, exist_ok=True)
    archive_path = backup_dir / f"demo_db_{timestamp:%Y%m%d_%H%M%S}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(database_path, arcname=database_path.name)
    return archive_path


def main() -> int:
    """Run the job and log source path, archive path, and observed byte sizes."""
    configure_logging()
    try:
        database_created = ensure_demo_database()
        archive_path = create_backup()
        logging.info(
            "database_path=%s database_created=%s database_size_bytes=%d "
            "archive_path=%s archive_size_bytes=%d",
            DATABASE_PATH,
            database_created,
            DATABASE_PATH.stat().st_size,
            archive_path,
            archive_path.stat().st_size,
        )
    except Exception:
        logging.exception(
            "backup_db_failed database_path=%s backup_dir=%s",
            DATABASE_PATH,
            BACKUP_DIR,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
