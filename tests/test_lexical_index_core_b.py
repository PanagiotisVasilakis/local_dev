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



def test_incremental_sync_preserves_unchanged_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("alpha\n")
    (repo / "b.py").write_text("beta\n")
    first = _snapshot(
        repo,
        [("a.py", RepositoryContentKind.TEXT), ("b.py", RepositoryContentKind.TEXT)],
    )
    database = _db(tmp_path)
    index = LexicalIndex(database)
    index.sync(first)

    with database.connect() as connection:
        before = connection.execute(
            "SELECT chunk_id FROM lexical_chunks WHERE path='a.py'"
        ).fetchone()[0]

    (repo / "b.py").write_text("beta changed\n")
    second = _snapshot(
        repo,
        [("a.py", RepositoryContentKind.TEXT), ("b.py", RepositoryContentKind.TEXT)],
    )
    result = index.sync(second)

    with database.connect() as connection:
        after = connection.execute(
            "SELECT chunk_id FROM lexical_chunks WHERE path='a.py'"
        ).fetchone()[0]

    assert result.rebuilt_paths == ("b.py",)
    assert result.unchanged_paths == ("a.py",)
    assert before == after


def test_removed_file_is_deleted_from_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("alpha\n")
    (repo / "b.py").write_text("orphanword\n")
    first = _snapshot(
        repo,
        [("a.py", RepositoryContentKind.TEXT), ("b.py", RepositoryContentKind.TEXT)],
    )
    index = LexicalIndex(_db(tmp_path))
    index.sync(first)

    (repo / "b.py").unlink()
    second = _snapshot(repo, [("a.py", RepositoryContentKind.TEXT)])
    result = index.sync(second)

    assert result.removed_paths == ("b.py",)
    assert index.search(second, "orphanword").hits == ()


def test_same_snapshot_sync_is_noop(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("alpha\n")
    snapshot = _snapshot(repo, [("a.py", RepositoryContentKind.TEXT)])
    index = LexicalIndex(_db(tmp_path))
    index.sync(snapshot)
    again = index.sync(snapshot)

    assert again.rebuilt_paths == ()
    assert again.removed_paths == ()
    assert again.unchanged_paths == ("a.py",)


def test_policy_change_rebuilds_all_candidate_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("alpha\n")
    (repo / "b.py").write_text("beta\n")
    snapshot = _snapshot(
        repo,
        [("a.py", RepositoryContentKind.TEXT), ("b.py", RepositoryContentKind.TEXT)],
    )
    database = _db(tmp_path)
    LexicalIndex(database, LexicalIndexPolicy(max_chunk_chars=100)).sync(snapshot)

    result = LexicalIndex(database, LexicalIndexPolicy(max_chunk_chars=80)).sync(snapshot)
    assert result.rebuilt_paths == ("a.py", "b.py")


