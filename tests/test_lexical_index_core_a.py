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



def test_migration_creates_fts5_schema(tmp_path: Path) -> None:
    database = _db(tmp_path)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='lexical_chunks_fts'"
        ).fetchone()
    assert row is not None
    assert "fts5" in row[0].lower()


def test_sync_and_search_text_content(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "budget.py").write_text("class BudgetGovernor:\n    pass\n")
    snapshot = _snapshot(repo, [("budget.py", RepositoryContentKind.TEXT)])

    index = LexicalIndex(_db(tmp_path))
    sync = index.sync(snapshot)
    response = index.search(snapshot, "BudgetGovernor")

    assert sync.rebuilt_paths == ("budget.py",)
    assert len(response.hits) == 1
    assert response.hits[0].path == "budget.py"
    assert "BudgetGovernor" in response.hits[0].content
    assert response.coverage_complete


def test_binary_files_are_not_indexed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("needle\n")
    (repo / "blob.bin").write_bytes(b"\x00needle\x00")
    snapshot = _snapshot(
        repo,
        [
            ("a.py", RepositoryContentKind.TEXT),
            ("blob.bin", RepositoryContentKind.BINARY),
        ],
    )
    index = LexicalIndex(_db(tmp_path))
    sync = index.sync(snapshot)

    assert sync.rebuilt_paths == ("a.py",)
    assert [hit.path for hit in index.search(snapshot, "needle").hits] == ["a.py"]


def test_empty_file_is_searchable_by_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "empty_config.toml").write_bytes(b"")
    snapshot = _snapshot(repo, [("empty_config.toml", RepositoryContentKind.EMPTY)])

    index = LexicalIndex(_db(tmp_path))
    index.sync(snapshot)
    response = index.search(snapshot, "empty_config")

    assert len(response.hits) == 1
    assert response.hits[0].path == "empty_config.toml"
    assert response.hits[0].content == ""


def test_stale_snapshot_is_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "a.py"
    target.write_text("alpha\n")
    first = _snapshot(repo, [("a.py", RepositoryContentKind.TEXT)])
    index = LexicalIndex(_db(tmp_path))
    index.sync(first)

    target.write_text("beta\n")
    second = _snapshot(repo, [("a.py", RepositoryContentKind.TEXT)])
    with pytest.raises(LexicalIndexStale):
        index.search(second, "beta")


