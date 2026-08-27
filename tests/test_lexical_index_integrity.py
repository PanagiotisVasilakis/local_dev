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



def test_multiple_repositories_share_database_without_cross_talk(tmp_path: Path) -> None:
    database = _db(tmp_path)
    snapshots = []
    for name, word in (("one", "alphaonly"), ("two", "betaonly")):
        repo = tmp_path / name
        repo.mkdir()
        (repo / "a.txt").write_text(word)
        snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
        LexicalIndex(database).sync(snapshot)
        snapshots.append(snapshot)

    index = LexicalIndex(database)
    assert index.search(snapshots[0], "betaonly").hits == ()
    assert index.search(snapshots[1], "alphaonly").hits == ()


def test_concurrent_same_snapshot_sync_serializes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("needle")
    snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    database = _db(tmp_path)

    def run():
        return LexicalIndex(database).sync(snapshot)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(run), pool.submit(run)]]

    assert sorted(len(result.rebuilt_paths) for result in results) == [0, 1]


def test_failed_changed_file_read_rolls_back_old_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "a.txt"
    target.write_text("oldword")
    first = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    database = _db(tmp_path)
    index = LexicalIndex(database)
    index.sync(first)

    target.write_text("newword")
    second = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    target.write_text("thirdword")

    with pytest.raises(RepositoryScanRaceError):
        index.sync(second)

    assert index.search(first, "oldword").hits
    assert index.search(first, "newword").hits == ()


def test_path_hit_is_boosted_over_content_only_hit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "budget.txt").write_text("other")
    (repo / "notes.txt").write_text("budget")
    snapshot = _snapshot(
        repo,
        [("budget.txt", RepositoryContentKind.TEXT), ("notes.txt", RepositoryContentKind.TEXT)],
    )
    index = LexicalIndex(_db(tmp_path))
    index.sync(snapshot)
    hits = index.search(snapshot, "budget").hits

    assert hits[0].path == "budget.txt"


def test_chunk_limit_fails_before_unbounded_growth(tmp_path: Path) -> None:
    repo = tmp_path / "repo-limit"
    repo.mkdir()
    (repo / "a.txt").write_text("abcdefghij")
    snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    index = LexicalIndex(
        _db(tmp_path),
        LexicalIndexPolicy(
            max_chunk_chars=1,
            chunk_lines=1,
            overlap_lines=0,
            max_chunks_per_file=3,
        ),
    )

    with pytest.raises(LexicalIndexError, match="more than 3"):
        index.sync(snapshot)


def test_search_detects_replaced_file_metadata(tmp_path: Path) -> None:
    repo = tmp_path / "repo-meta-search"
    repo.mkdir()
    (repo / "a.txt").write_text("needle")
    snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    database = _db(tmp_path)
    index = LexicalIndex(database)
    index.sync(snapshot)

    with database.connect() as connection:
        row = connection.execute(
            "SELECT repository_id, path, size_bytes, language, content_kind "
            "FROM lexical_files"
        ).fetchone()
        connection.execute("DELETE FROM lexical_chunks")
        connection.execute("DELETE FROM lexical_files")
        connection.execute(
            """
            INSERT INTO lexical_files(
                repository_id, path, file_sha256, size_bytes, language,
                content_kind, status, decode_lossy, chunk_count
            ) VALUES (?, ?, ?, ?, ?, ?, 'indexed', 0, 1)
            """,
            (
                row["repository_id"],
                row["path"],
                "0" * 64,
                row["size_bytes"],
                row["language"],
                row["content_kind"],
            ),
        )

    with pytest.raises(LexicalIndexError, match="metadata"):
        index.search(snapshot, "needle")


def test_search_detects_per_file_chunk_count_drift(tmp_path: Path) -> None:
    repo = tmp_path / "repo-chunk-count"
    repo.mkdir()
    (repo / "a.txt").write_text("needle")
    snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    database = _db(tmp_path)
    index = LexicalIndex(database)
    index.sync(snapshot)

    with database.connect() as connection:
        connection.execute("DELETE FROM lexical_chunks")
        connection.execute("UPDATE lexical_index_state SET chunk_count=0")

    with pytest.raises(LexicalIndexError, match="chunk count"):
        index.search(snapshot, "needle")


def test_snapshot_reader_enforces_explicit_size_bound(tmp_path: Path) -> None:
    repo = tmp_path / "repo-reader-limit"
    repo.mkdir()
    (repo / "a.txt").write_text("12345")
    snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])

    with pytest.raises(RepositoryScanError, match="read limit"):
        read_snapshot_file(snapshot, "a.txt", max_bytes=4)


