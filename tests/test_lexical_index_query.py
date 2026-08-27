from __future__ import annotations

import hashlib
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from local_dev.db import Database
from local_dev.repository.contracts import (
    RepositoryContentKind,
    RepositoryEntry,
    RepositoryEntryKind,
    RepositoryScanError,
    RepositoryScanRaceError,
    RepositorySnapshot,
    repository_fingerprint,
)
from local_dev.repository.scanner import RepositoryScanner
from local_dev.repository.reader import read_snapshot_file
from local_dev.retrieval.contracts import (
    LexicalIndexError,
    LexicalIndexNotReady,
    LexicalIndexPolicy,
    LexicalIndexStale,
    LexicalQueryError,
)
from local_dev.retrieval.index import LexicalIndex


def _entry(
    root: Path,
    rel: str,
    *,
    kind: RepositoryContentKind = RepositoryContentKind.TEXT,
) -> RepositoryEntry:
    data = (root / rel).read_bytes()
    return RepositoryEntry(
        path=rel,
        kind=RepositoryEntryKind.FILE,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        executable=False,
        content_kind=kind if data else RepositoryContentKind.EMPTY,
        language="Python" if rel.endswith(".py") else None,
    )


def _snapshot(root: Path, paths: list[tuple[str, RepositoryContentKind]]) -> RepositorySnapshot:
    entries = tuple(
        sorted(
            (_entry(root, path, kind=kind) for path, kind in paths),
            key=lambda entry: entry.path,
        )
    )
    return RepositorySnapshot(
        repository_root=root.resolve(),
        entries=entries,
        fingerprint_sha256=repository_fingerprint(entries),
    )


def _db(tmp_path: Path) -> Database:
    database = Database(
        tmp_path / "state.db",
        Path(__file__).parents[1] / "src/local_dev/migrations",
    )
    database.migrate()
    return database



def test_query_operators_are_not_executed_as_raw_fts_syntax(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("foo bar\n")
    snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    index = LexicalIndex(_db(tmp_path))
    index.sync(snapshot)

    assert index.search(snapshot, 'foo" OR *').hits == ()


@pytest.mark.parametrize("query", ["", "   ", "***", "___"])
def test_query_without_searchable_terms_is_rejected(tmp_path: Path, query: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("x")
    snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    index = LexicalIndex(_db(tmp_path))
    index.sync(snapshot)

    with pytest.raises(LexicalQueryError):
        index.search(snapshot, query)


def test_query_and_limit_bounds_are_enforced(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("a b c d")
    snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    policy = LexicalIndexPolicy(max_query_terms=2, max_results=3, default_limit=2)
    index = LexicalIndex(_db(tmp_path), policy)
    index.sync(snapshot)

    with pytest.raises(LexicalQueryError):
        index.search(snapshot, "a b c")
    with pytest.raises(ValueError):
        index.search(snapshot, "a", limit=4)


def test_chunk_ids_are_root_independent(tmp_path: Path) -> None:
    ids = []
    for suffix in ("one", "two"):
        repo = tmp_path / suffix / "repo"
        repo.mkdir(parents=True)
        (repo / "a.py").write_text("needle\n")
        snapshot = _snapshot(repo, [("a.py", RepositoryContentKind.TEXT)])
        database = Database(
            tmp_path / suffix / "state.db",
            Path(__file__).parents[1] / "src/local_dev/migrations",
        )
        database.migrate()
        index = LexicalIndex(database)
        index.sync(snapshot)
        ids.append(index.search(snapshot, "needle").hits[0].chunk_id)
    assert ids[0] == ids[1]


def test_deterministic_tie_order_uses_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "b.txt").write_text("needle\n")
    (repo / "a.txt").write_text("needle\n")
    snapshot = _snapshot(
        repo,
        [("a.txt", RepositoryContentKind.TEXT), ("b.txt", RepositoryContentKind.TEXT)],
    )
    index = LexicalIndex(_db(tmp_path))
    index.sync(snapshot)

    assert [hit.path for hit in index.search(snapshot, "needle").hits] == ["a.txt", "b.txt"]


def test_fts_row_count_tampering_is_detected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("needle\n")
    snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    database = _db(tmp_path)
    index = LexicalIndex(database)
    index.sync(snapshot)

    with database.connect() as connection:
        connection.execute("DELETE FROM lexical_chunks_fts")

    with pytest.raises(LexicalIndexError, match="FTS vocabulary digest"):
        index.search(snapshot, "needle")


def test_lexical_file_metadata_update_is_blocked_by_database(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("needle\n")
    snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    database = _db(tmp_path)
    LexicalIndex(database).sync(snapshot)

    with pytest.raises(sqlite3.IntegrityError, match="replace-only"):
        with database.connect() as connection:
            connection.execute(
                "UPDATE lexical_files SET file_sha256=?",
                ("0" * 64,),
            )


def test_missing_migration_schema_is_reported_not_ready(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("needle")
    snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    database = Database(tmp_path / "unmigrated.db", tmp_path / "missing")
    index = LexicalIndex(database)

    with pytest.raises(LexicalIndexNotReady):
        index.search(snapshot, "needle")


def test_long_line_is_split_to_chunk_bound(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("needle " * 20)
    snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    index = LexicalIndex(
        _db(tmp_path),
        LexicalIndexPolicy(max_chunk_chars=25, chunk_lines=10, overlap_lines=1),
    )
    result = index.sync(snapshot)
    response = index.search(snapshot, "needle")

    assert result.chunk_count > 1
    assert all(len(hit.content) <= 25 for hit in response.hits)


def test_snake_case_query_is_tokenized_compatibly(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def budget_governor():\n    pass\n")
    snapshot = _snapshot(repo, [("a.py", RepositoryContentKind.TEXT)])
    index = LexicalIndex(_db(tmp_path))
    index.sync(snapshot)

    assert index.search(snapshot, "budget_governor").hits
    assert index.search(snapshot, "budget governor").hits


