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



def test_oversized_text_file_is_explicitly_reported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "huge.txt").write_text("needle " * 100)
    snapshot = _snapshot(repo, [("huge.txt", RepositoryContentKind.TEXT)])
    index = LexicalIndex(_db(tmp_path), LexicalIndexPolicy(max_file_bytes=32))
    result = index.sync(snapshot)
    response = index.search(snapshot, "needle")

    assert result.skipped_paths == ("huge.txt",)
    assert response.skipped_paths == ("huge.txt",)
    assert not response.coverage_complete
    assert response.hits == ()


def test_lossy_utf8_decode_is_reported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "latin.txt").write_bytes(b"caf\xe9 needle")
    snapshot = _snapshot(repo, [("latin.txt", RepositoryContentKind.TEXT)])
    index = LexicalIndex(_db(tmp_path))
    result = index.sync(snapshot)
    response = index.search(snapshot, "needle")

    assert result.lossy_paths == ("latin.txt",)
    assert response.lossy_paths == ("latin.txt",)
    assert response.hits[0].decode_lossy
    assert "\ufffd" in response.hits[0].content


def test_snapshot_reader_rejects_content_change(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("one")
    snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    (repo / "a.txt").write_text("two")

    with pytest.raises(RepositoryScanRaceError):
        read_snapshot_file(snapshot, "a.txt", max_bytes=100)


