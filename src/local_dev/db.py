from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class Database:
    """Small SQLite boundary with deterministic migrations and sane local defaults."""

    def __init__(self, path: Path, migrations_dir: Path) -> None:
        self._path = path
        self._migrations_dir = migrations_dir

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            applied = {
                row["version"]: row["checksum"]
                for row in connection.execute(
                    "SELECT version, checksum FROM schema_migrations"
                ).fetchall()
            }
            for migration in sorted(self._migrations_dir.glob("*.sql")):
                sql = migration.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                previous = applied.get(migration.name)
                if previous is not None:
                    if previous != checksum:
                        raise RuntimeError(
                            f"applied migration changed on disk: {migration.name}"
                        )
                    continue
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_migrations(version, checksum) VALUES (?, ?)",
                    (migration.name, checksum),
                )
