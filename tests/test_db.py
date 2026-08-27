import sqlite3
from pathlib import Path

import pytest

from local_dev.db import Database


def _migration_dir(tmp_path: Path) -> Path:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    return migrations_dir


def test_database_migrations_are_idempotent(tmp_path: Path) -> None:
    migrations_dir = _migration_dir(tmp_path)
    (migrations_dir / "001_sample.sql").write_text(
        "CREATE TABLE sample(id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )
    database = Database(tmp_path / "state.db", migrations_dir)

    database.migrate()
    database.migrate()

    with database.connect() as connection:
        row = connection.execute(
            "SELECT version, filename FROM schema_migrations"
        ).fetchone()
    assert tuple(row) == (1, "001_sample.sql")


def test_database_rejects_changed_applied_migration(tmp_path: Path) -> None:
    migrations_dir = _migration_dir(tmp_path)
    migration = migrations_dir / "001_sample.sql"
    migration.write_text("CREATE TABLE sample(id INTEGER PRIMARY KEY);", encoding="utf-8")
    database = Database(tmp_path / "state.db", migrations_dir)
    database.migrate()

    migration.write_text("CREATE TABLE changed(id INTEGER PRIMARY KEY);", encoding="utf-8")

    with pytest.raises(RuntimeError, match="changed on disk"):
        database.migrate()


def test_database_rejects_removed_applied_migration(tmp_path: Path) -> None:
    migrations_dir = _migration_dir(tmp_path)
    migration = migrations_dir / "001_sample.sql"
    migration.write_text("CREATE TABLE sample(id INTEGER PRIMARY KEY);", encoding="utf-8")
    database = Database(tmp_path / "state.db", migrations_dir)
    database.migrate()
    migration.unlink()
    (migrations_dir / "002_next.sql").write_text(
        "CREATE TABLE next_table(id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="missing from disk"):
        database.migrate()


def test_database_rejects_invalid_migration_filename(tmp_path: Path) -> None:
    migrations_dir = _migration_dir(tmp_path)
    (migrations_dir / "1.sql").write_text("SELECT 1;", encoding="utf-8")
    database = Database(tmp_path / "state.db", migrations_dir)

    with pytest.raises(RuntimeError, match="invalid migration filename"):
        database.migrate()


def test_database_rejects_duplicate_migration_version(tmp_path: Path) -> None:
    migrations_dir = _migration_dir(tmp_path)
    (migrations_dir / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (migrations_dir / "001_second.sql").write_text("SELECT 2;", encoding="utf-8")
    database = Database(tmp_path / "state.db", migrations_dir)

    with pytest.raises(RuntimeError, match="duplicate migration version"):
        database.migrate()


def test_database_rolls_back_failed_migration_atomically(tmp_path: Path) -> None:
    migrations_dir = _migration_dir(tmp_path)
    (migrations_dir / "001_broken.sql").write_text(
        "CREATE TABLE should_not_survive(id INTEGER PRIMARY KEY);\n"
        "INSERT INTO missing_table(id) VALUES (1);",
        encoding="utf-8",
    )
    database = Database(tmp_path / "state.db", migrations_dir)

    with pytest.raises(sqlite3.OperationalError):
        database.migrate()

    connection = sqlite3.connect(tmp_path / "state.db")
    try:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='should_not_survive'"
        ).fetchone()
        migration_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
    finally:
        connection.close()
    assert table is None
    assert migration_table is None


def test_database_rejects_transaction_control_inside_migration(tmp_path: Path) -> None:
    migrations_dir = _migration_dir(tmp_path)
    (migrations_dir / "001_bad.sql").write_text(
        "BEGIN;\nCREATE TABLE sample(id INTEGER PRIMARY KEY);\nCOMMIT;",
        encoding="utf-8",
    )
    database = Database(tmp_path / "state.db", migrations_dir)

    with pytest.raises(RuntimeError, match="must not manage transactions"):
        database.migrate()


def test_database_requires_migration_directory(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db", tmp_path / "missing")
    with pytest.raises(FileNotFoundError, match="migrations directory"):
        database.migrate()


def test_database_allows_trigger_blocks_inside_atomic_migration(tmp_path: Path) -> None:
    migrations_dir = _migration_dir(tmp_path)
    (migrations_dir / "001_trigger.sql").write_text(
        "CREATE TABLE source(id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE audit(source_id INTEGER NOT NULL);\n"
        "CREATE TRIGGER source_audit AFTER INSERT ON source\n"
        "BEGIN\n"
        "    INSERT INTO audit(source_id) VALUES (NEW.id);\n"
        "END;",
        encoding="utf-8",
    )
    database = Database(tmp_path / "state.db", migrations_dir)
    database.migrate()

    with database.connect() as connection:
        connection.execute("INSERT INTO source(id) VALUES (1)")
        count = connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    assert count == 1


def test_database_rejects_zero_migration_version(tmp_path: Path) -> None:
    migrations_dir = _migration_dir(tmp_path)
    (migrations_dir / "000_zero.sql").write_text("SELECT 1;", encoding="utf-8")
    database = Database(tmp_path / "state.db", migrations_dir)
    with pytest.raises(RuntimeError, match="version must be positive"):
        database.migrate()


def test_database_rolls_back_all_pending_migrations_together(tmp_path: Path) -> None:
    migrations_dir = _migration_dir(tmp_path)
    (migrations_dir / "001_first.sql").write_text(
        "CREATE TABLE first_table(id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )
    (migrations_dir / "002_broken.sql").write_text(
        "CREATE TABLE second_table(id INTEGER PRIMARY KEY);\n"
        "INSERT INTO missing_table(id) VALUES (1);",
        encoding="utf-8",
    )
    database = Database(tmp_path / "state.db", migrations_dir)

    with pytest.raises(sqlite3.OperationalError):
        database.migrate()

    connection = sqlite3.connect(tmp_path / "state.db")
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        connection.close()
    assert "first_table" not in tables
    assert "second_table" not in tables
    assert "schema_migrations" not in tables


def test_database_rejects_backfilled_migration_version(tmp_path: Path) -> None:
    migrations_dir = _migration_dir(tmp_path)
    (migrations_dir / "002_second.sql").write_text("SELECT 2;", encoding="utf-8")
    database = Database(tmp_path / "state.db", migrations_dir)
    database.migrate()
    (migrations_dir / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(RuntimeError, match="must be appended"):
        database.migrate()


def test_database_connect_commits_and_rolls_back(tmp_path: Path) -> None:
    migrations_dir = _migration_dir(tmp_path)
    (migrations_dir / "001_items.sql").write_text(
        "CREATE TABLE items(id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )
    database = Database(tmp_path / "state.db", migrations_dir)
    database.migrate()

    with database.connect() as connection:
        connection.execute("INSERT INTO items(id) VALUES (1)")

    with pytest.raises(RuntimeError, match="force rollback"):
        with database.connect() as connection:
            connection.execute("INSERT INTO items(id) VALUES (2)")
            raise RuntimeError("force rollback")

    with database.connect() as connection:
        ids = [row[0] for row in connection.execute("SELECT id FROM items ORDER BY id")]
    assert ids == [1]


def test_concurrent_migrate_calls_do_not_race(tmp_path: Path) -> None:
    import threading

    migrations_dir = _migration_dir(tmp_path)
    (migrations_dir / "001_sample.sql").write_text(
        "CREATE TABLE sample(id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )
    database_path = tmp_path / "state.db"
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            barrier.wait()
            Database(database_path, migrations_dir).migrate()
        except BaseException as exc:  # pragma: no cover - only populated on failure
            errors.append(exc)

    threads = [threading.Thread(target=run), threading.Thread(target=run)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert errors == []
    with Database(database_path, migrations_dir).connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert count == 1


def test_database_uses_bundled_migrations_by_default(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()

    with database.connect() as connection:
        generation = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_generation'"
        ).fetchone()[0]
        migration = connection.execute(
            "SELECT version, filename FROM schema_migrations"
        ).fetchone()
    assert generation == "1"
    assert tuple(migration) == (1, "001_initial.sql")
