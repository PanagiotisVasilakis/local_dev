from pathlib import Path

from local_dev.db import Database


def test_database_migrations_are_idempotent(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001.sql").write_text(
        "CREATE TABLE sample(id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )
    database = Database(tmp_path / "state.db", migrations_dir)

    database.migrate()
    database.migrate()

    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert count == 1


def test_database_rejects_changed_applied_migration(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    migration = migrations_dir / "001.sql"
    migration.write_text("CREATE TABLE sample(id INTEGER PRIMARY KEY);", encoding="utf-8")
    database = Database(tmp_path / "state.db", migrations_dir)
    database.migrate()

    migration.write_text("CREATE TABLE changed(id INTEGER PRIMARY KEY);", encoding="utf-8")

    try:
        database.migrate()
    except RuntimeError as exc:
        assert "changed on disk" in str(exc)
    else:
        raise AssertionError("changed migration must fail closed")
