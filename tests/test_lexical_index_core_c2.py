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



def test_snapshot_reader_rejects_final_symlink_swap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("one")
    outside = tmp_path / "outside.txt"
    outside.write_text("one")
    snapshot = _snapshot(repo, [("a.txt", RepositoryContentKind.TEXT)])
    (repo / "a.txt").unlink()
    (repo / "a.txt").symlink_to(outside)

    with pytest.raises(RepositoryScanRaceError):
        read_snapshot_file(snapshot, "a.txt", max_bytes=100)


def test_snapshot_reader_rejects_intermediate_symlink_swap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "pkg"
    nested.mkdir(parents=True)
    (nested / "a.txt").write_text("one")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_text("one")
    snapshot = _snapshot(repo, [("pkg/a.txt", RepositoryContentKind.TEXT)])

    (nested / "a.txt").unlink()
    nested.rmdir()
    nested.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RepositoryScanRaceError):
        read_snapshot_file(snapshot, "pkg/a.txt", max_bytes=100)


@pytest.mark.skipif(os.name != "posix", reason="POSIX filename byte semantics")
def test_non_utf8_filename_round_trips_through_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    raw = os.fsencode(repo) + b"/bad-\xff.py"
    fd = os.open(raw, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, b"needle\n")
    finally:
        os.close(fd)
    name = os.listdir(repo)[0]
    raw_path = os.fsencode(repo / name)
    descriptor = os.open(raw_path, os.O_RDONLY)
    try:
        payload = os.read(descriptor, 1024)
    finally:
        os.close(descriptor)
    entry = RepositoryEntry(
        path=name,
        kind=RepositoryEntryKind.FILE,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        executable=False,
        content_kind=RepositoryContentKind.TEXT,
        language="Python",
    )
    snapshot = RepositorySnapshot(
        repository_root=repo.resolve(),
        entries=(entry,),
        fingerprint_sha256=repository_fingerprint((entry,)),
    )
    index = LexicalIndex(_db(tmp_path))
    index.sync(snapshot)
    response = index.search(snapshot, "needle")

    assert response.hits[0].path == name


