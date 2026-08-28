from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from local_dev.db import Database
from local_dev.repository.contracts import (
    RepositoryContentKind,
    RepositoryEntry,
    RepositoryEntryKind,
    RepositorySnapshot,
    repository_fingerprint,
)
from local_dev.structure import StructuralIndex, StructuralIndexError, StructuralIndexNotReady


def _fixture(tmp_path: Path) -> tuple[Database, RepositorySnapshot, StructuralIndex]:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "a.py"
    target.write_text(
        "class A:\n"
        "    def m(self):\n"
        "        import os\n"
        "        return 1\n"
    )
    data = target.read_bytes()
    entry = RepositoryEntry(
        "a.py",
        RepositoryEntryKind.FILE,
        len(data),
        hashlib.sha256(data).hexdigest(),
        False,
        RepositoryContentKind.TEXT,
        "Python",
    )
    snap = RepositorySnapshot(repo.resolve(), (entry,), repository_fingerprint((entry,)))
    database = Database(
        tmp_path / "state.db",
        Path(__file__).parents[1] / "src/local_dev/migrations",
    )
    database.migrate()
    index = StructuralIndex(database)
    index.sync(snap)
    return database, snap, index


def test_parent_delete_is_blocked_by_foreign_key(tmp_path: Path) -> None:
    database, _, _ = _fixture(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with database.immediate_transaction() as connection:
            connection.execute("DELETE FROM structural_symbols WHERE qualified_name='A'")


def test_wrong_symbol_file_hash_is_rejected(tmp_path: Path) -> None:
    database, _, _ = _fixture(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with database.immediate_transaction() as connection:
            row = connection.execute(
                "SELECT repository_id, path FROM structural_files"
            ).fetchone()
            connection.execute(
                """
                INSERT INTO structural_symbols(
                    repository_id, path, symbol_id, file_sha256, kind, name,
                    qualified_name, start_line, start_col, end_line, end_col,
                    decorators_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row[0],
                    row[1],
                    "2" * 64,
                    "3" * 64,
                    "variable",
                    "x",
                    "x",
                    1,
                    0,
                    1,
                    1,
                    "[]",
                ),
            )


def test_plain_import_requires_matching_non_null_module(tmp_path: Path) -> None:
    database, _, _ = _fixture(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with database.immediate_transaction() as connection:
            row = connection.execute(
                "SELECT repository_id, path, file_sha256 FROM structural_files"
            ).fetchone()
            connection.execute(
                """
                INSERT INTO structural_imports(
                    repository_id, path, import_id, file_sha256, kind, module,
                    name, level, line, col, ordinal
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (row[0], row[1], "5" * 64, row[2], "import", None, "sys", 0, 9, 0, 99),
            )


def test_replace_only_file_metadata_is_enforced(tmp_path: Path) -> None:
    database, _, _ = _fixture(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with database.immediate_transaction() as connection:
            connection.execute("UPDATE structural_files SET size_bytes = size_bytes + 1")


def test_state_count_tamper_is_detected(tmp_path: Path) -> None:
    database, snap, index = _fixture(tmp_path)
    with database.immediate_transaction() as connection:
        connection.execute("UPDATE structural_index_state SET symbol_count = symbol_count + 1")
    with pytest.raises(StructuralIndexError):
        index.symbols(snap)


def test_state_digest_tamper_is_detected(tmp_path: Path) -> None:
    database, snap, index = _fixture(tmp_path)
    with database.immediate_transaction() as connection:
        connection.execute(
            "UPDATE structural_index_state SET structure_digest = ?",
            ("f" * 64,),
        )
    with pytest.raises(StructuralIndexError):
        index.imports(snap)


def test_malformed_state_digest_is_typed_error(tmp_path: Path) -> None:
    database, snap, index = _fixture(tmp_path)
    with database.immediate_transaction() as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE structural_index_state SET structure_digest = ?",
            ("z" * 64,),
        )
    with pytest.raises(StructuralIndexError):
        index.files(snap)


def test_partial_schema_is_reported_as_not_ready(tmp_path: Path) -> None:
    database, snap, index = _fixture(tmp_path)
    with database.immediate_transaction() as connection:
        connection.execute("DROP TABLE structural_imports")
    with pytest.raises(StructuralIndexNotReady):
        index.symbols(snap)


def test_wrong_import_scope_qualified_name_is_rejected(tmp_path: Path) -> None:
    database, snap, index = _fixture(tmp_path)
    scope = index.symbols(snap, qualified_name="A.m")[0]
    with pytest.raises(sqlite3.IntegrityError):
        with database.immediate_transaction() as connection:
            row = connection.execute(
                "SELECT repository_id, path, file_sha256 FROM structural_files"
            ).fetchone()
            connection.execute(
                """
                INSERT INTO structural_imports(
                    repository_id, path, import_id, file_sha256, kind, module,
                    name, level, scope_symbol_id, scope_qualified_name, line, col, ordinal
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row[0],
                    row[1],
                    "4" * 64,
                    row[2],
                    "import",
                    "sys",
                    "sys",
                    0,
                    scope.symbol_id,
                    "WRONG",
                    9,
                    0,
                    99,
                ),
            )
