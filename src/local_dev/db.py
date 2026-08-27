from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_MIGRATION_NAME = re.compile(r"^(?P<version>\d{3,})_(?P<name>[a-z0-9][a-z0-9_]*)\.sql$")


@dataclass(frozen=True, slots=True)
class _Migration:
    version: int
    filename: str
    sql: str
    checksum: str


class Database:
    """Small SQLite boundary with fail-closed, append-only migrations."""

    def __init__(self, path: Path, migrations_dir: Path | None = None) -> None:
        self._path = path
        self._migrations_dir = migrations_dir or Path(__file__).with_name("migrations")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = self._open_connection()
        # Configure PRAGMAs in SQLite autocommit mode, then use PEP 249 transactions.
        connection.autocommit = False
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        migrations = self._discover_migrations()
        connection = self._open_connection()
        try:
            # Lock before reading migration state so concurrent starters cannot race.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    filename TEXT NOT NULL UNIQUE,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            applied_rows = connection.execute(
                "SELECT version, filename, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            applied = {int(row["version"]): row for row in applied_rows}
            on_disk = {migration.version: migration for migration in migrations}

            missing = sorted(set(applied) - set(on_disk))
            if missing:
                raise RuntimeError(
                    "applied migrations missing from disk: "
                    + ", ".join(str(version) for version in missing)
                )

            for version, row in applied.items():
                migration = on_disk[version]
                if row["filename"] != migration.filename:
                    raise RuntimeError(
                        f"applied migration renamed on disk: {row['filename']} -> "
                        f"{migration.filename}"
                    )
                if row["checksum"] != migration.checksum:
                    raise RuntimeError(
                        f"applied migration changed on disk: {migration.filename}"
                    )

            highest_applied = max(applied, default=0)
            pending = [migration for migration in migrations if migration.version not in applied]
            if pending and pending[0].version < highest_applied:
                raise RuntimeError("new migration must be appended after all applied migrations")

            for migration in pending:
                self._execute_migration(connection, migration)
                connection.execute(
                    "INSERT INTO schema_migrations(version, filename, checksum) VALUES (?, ?, ?)",
                    (migration.version, migration.filename, migration.checksum),
                )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _execute_migration(
        self, connection: sqlite3.Connection, migration: _Migration
    ) -> None:
        # Migration files may contain triggers, but may not alter transaction boundaries.
        connection.set_authorizer(_migration_authorizer)
        try:
            connection.executescript(migration.sql)
        except sqlite3.DatabaseError as exc:
            if "not authorized" in str(exc).lower():
                raise RuntimeError(
                    f"migration must not manage transactions explicitly: {migration.filename}"
                ) from exc
            raise
        finally:
            connection.set_authorizer(None)

    def _open_connection(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # autocommit=True lets this boundary own explicit BEGIN/COMMIT semantics.
        connection = sqlite3.connect(self._path, timeout=5.0, autocommit=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        journal_row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
        if journal_row is None:
            connection.close()
            raise RuntimeError("SQLite did not report a journal mode")
        journal_mode = journal_row[0]
        if str(journal_mode).lower() != "wal":
            connection.close()
            raise RuntimeError(f"SQLite WAL mode unavailable: {journal_mode}")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _discover_migrations(self) -> list[_Migration]:
        if not self._migrations_dir.is_dir():
            raise FileNotFoundError(f"migrations directory not found: {self._migrations_dir}")

        migration_paths = sorted(self._migrations_dir.glob("*.sql"))
        if not migration_paths:
            raise RuntimeError(f"no migrations found in: {self._migrations_dir}")

        migrations: list[_Migration] = []
        seen_versions: set[int] = set()
        for path in migration_paths:
            match = _MIGRATION_NAME.fullmatch(path.name)
            if match is None:
                raise RuntimeError(
                    f"invalid migration filename {path.name!r}; expected NNN_name.sql"
                )
            version = int(match.group("version"))
            if version <= 0:
                raise RuntimeError(f"migration version must be positive: {path.name}")
            if version in seen_versions:
                raise RuntimeError(f"duplicate migration version: {version}")
            seen_versions.add(version)

            sql = path.read_text(encoding="utf-8")
            if not sql.strip() or not sqlite3.complete_statement(sql):
                raise RuntimeError(f"migration must contain complete SQL: {path.name}")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            migrations.append(
                _Migration(
                    version=version,
                    filename=path.name,
                    sql=sql,
                    checksum=checksum,
                )
            )

        migrations.sort(key=lambda migration: migration.version)
        return migrations


def _migration_authorizer(
    action_code: int,
    _arg1: str | None,
    _arg2: str | None,
    _database_name: str | None,
    _trigger_name: str | None,
) -> int:
    if action_code in {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT}:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK
