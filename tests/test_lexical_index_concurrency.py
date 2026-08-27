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



def test_ranking_is_repository_local_and_stable_after_other_repo_indexed(
    tmp_path: Path,
) -> None:
    database = _db(tmp_path)

    first_repo = tmp_path / "rank-one"
    first_repo.mkdir()
    (first_repo / "budget.txt").write_text("other")
    (first_repo / "notes.txt").write_text("budget budget")
    first_snapshot = _snapshot(
        first_repo,
        [
            ("budget.txt", RepositoryContentKind.TEXT),
            ("notes.txt", RepositoryContentKind.TEXT),
        ],
    )
    index = LexicalIndex(database)
    index.sync(first_snapshot)
    before = [
        (hit.path, hit.score, hit.chunk_id)
        for hit in index.search(first_snapshot, "budget").hits
    ]

    second_repo = tmp_path / "rank-two"
    second_repo.mkdir()
    for number in range(25):
        (second_repo / f"file-{number}.txt").write_text("budget " * 100)
    second_snapshot = _snapshot(
        second_repo,
        [
            (f"file-{number}.txt", RepositoryContentKind.TEXT)
            for number in range(25)
        ],
    )
    index.sync(second_snapshot)

    after = [
        (hit.path, hit.score, hit.chunk_id)
        for hit in index.search(first_snapshot, "budget").hits
    ]
    assert after == before


def test_candidate_cap_fails_closed_instead_of_silent_truncation(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "candidate-cap"
    repo.mkdir()
    for number in range(4):
        (repo / f"{number}.txt").write_text("needle")
    snapshot = _snapshot(
        repo,
        [
            (f"{number}.txt", RepositoryContentKind.TEXT)
            for number in range(4)
        ],
    )
    index = LexicalIndex(
        _db(tmp_path),
        LexicalIndexPolicy(max_candidates=3),
    )
    index.sync(snapshot)

    with pytest.raises(LexicalQueryError, match="refine"):
        index.search(snapshot, "needle")


def test_search_remains_read_only_during_concurrent_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import local_dev.retrieval.index as index_module

    repo = tmp_path / "concurrent-read"
    repo.mkdir()
    target = repo / "a.txt"
    target.write_text("oldword")
    first = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    database = _db(tmp_path)
    index = LexicalIndex(database)
    index.sync(first)

    target.write_text("newword")
    second = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])

    started = threading.Event()
    release = threading.Event()
    original_reader = index_module.read_snapshot_file

    def blocking_reader(*args: object, **kwargs: object) -> bytes:
        started.set()
        assert release.wait(timeout=5)
        return original_reader(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(index_module, "read_snapshot_file", blocking_reader)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(index.sync, second)
        assert started.wait(timeout=5)
        old_response = index.search(first, "oldword")
        release.set()
        future.result(timeout=5)

    assert old_response.hits
    assert index.search(second, "newword").hits

