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



def test_deterministic_score_tracks_unicode61_diacritic_matching(tmp_path: Path) -> None:
    repo = tmp_path / "diacritics"
    repo.mkdir()
    (repo / "one.txt").write_text("café")
    (repo / "two.txt").write_text("cafe elsewhere")
    snapshot = _snapshot(
        repo,
        [
            ("one.txt", RepositoryContentKind.TEXT),
            ("two.txt", RepositoryContentKind.TEXT),
        ],
    )
    index = LexicalIndex(_db(tmp_path))
    index.sync(snapshot)
    hits = index.search(snapshot, "cafe").hits

    assert {hit.path for hit in hits} == {"one.txt", "two.txt"}
    assert all(hit.score > 0 for hit in hits)



def test_real_scanner_snapshot_indexes_end_to_end(tmp_path: Path) -> None:
    repo = tmp_path / "real-scanner"
    repo.mkdir()
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src/app.py").write_text("class RetrievalEngine:\n    pass\n", encoding="utf-8")
    (repo / "ignored").mkdir()
    (repo / "ignored/secret.txt").write_text("must-not-index", encoding="utf-8")

    snapshot = RepositoryScanner().scan(repo.resolve())
    index = LexicalIndex(_db(tmp_path))
    result = index.sync(snapshot)
    response = index.search(snapshot, "RetrievalEngine")

    assert "src/app.py" in result.rebuilt_paths
    assert [hit.path for hit in response.hits] == ["src/app.py"]
    assert index.search(snapshot, "must-not-index").hits == ()


def test_identical_repositories_can_share_one_database(tmp_path: Path) -> None:
    database = _db(tmp_path)
    snapshots: list[RepositorySnapshot] = []
    for name in ("checkout-one", "checkout-two"):
        repo = tmp_path / name
        repo.mkdir()
        (repo / "same.txt").write_text("needle\n", encoding="utf-8")
        snapshot = RepositoryScanner().scan(repo.resolve())
        LexicalIndex(database).sync(snapshot)
        snapshots.append(snapshot)

    first = LexicalIndex(database).search(snapshots[0], "needle")
    second = LexicalIndex(database).search(snapshots[1], "needle")

    assert len(first.hits) == 1
    assert len(second.hits) == 1
    assert first.hits[0].chunk_id == second.hits[0].chunk_id


def test_utf16_and_utf32_files_flow_from_scanner_to_retrieval(tmp_path: Path) -> None:
    repo = tmp_path / "unicode-repo"
    repo.mkdir()
    (repo / "utf16.txt").write_text("alpha needle\n", encoding="utf-16")
    (repo / "utf32.txt").write_text("beta marker\n", encoding="utf-32")

    snapshot = RepositoryScanner().scan(repo.resolve())
    by_path = {entry.path: entry for entry in snapshot.entries}
    assert by_path["utf16.txt"].content_kind is RepositoryContentKind.TEXT
    assert by_path["utf32.txt"].content_kind is RepositoryContentKind.TEXT

    index = LexicalIndex(_db(tmp_path))
    result = index.sync(snapshot)

    assert result.lossy_paths == ()
    assert [hit.path for hit in index.search(snapshot, "needle").hits] == ["utf16.txt"]
    assert [hit.path for hit in index.search(snapshot, "marker").hits] == ["utf32.txt"]


def test_sync_without_lexical_migration_is_reported_not_ready(tmp_path: Path) -> None:
    repo = tmp_path / "no-schema-repo"
    repo.mkdir()
    (repo / "a.txt").write_text("needle", encoding="utf-8")
    snapshot = RepositoryScanner().scan(repo.resolve())

    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_base.sql").write_text("CREATE TABLE base(x INTEGER);", encoding="utf-8")
    database = Database(tmp_path / "no-lexical.db", migrations)
    database.migrate()

    with pytest.raises(LexicalIndexNotReady):
        LexicalIndex(database).sync(snapshot)




def test_unicode_repository_path_is_searchable_as_path_text(tmp_path: Path) -> None:
    repo = tmp_path / "unicode-path"
    repo.mkdir()
    (repo / "café_module.py").write_text("unrelated body\n", encoding="utf-8")

    snapshot = RepositoryScanner().scan(repo.resolve())
    index = LexicalIndex(_db(tmp_path))
    index.sync(snapshot)

    hits = index.search(snapshot, "cafe").hits

    assert [hit.path for hit in hits] == ["café_module.py"]
    assert hits[0].score > 0

def test_index_format_revision_is_part_of_durable_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import local_dev.retrieval.index as index_module

    repo = tmp_path / "format-revision"
    repo.mkdir()
    (repo / "a.txt").write_text("needle", encoding="utf-8")
    snapshot = RepositoryScanner().scan(repo.resolve())
    database = _db(tmp_path)
    LexicalIndex(database).sync(snapshot)

    monkeypatch.setattr(index_module, "_INDEX_FORMAT_REVISION", 2)
    with pytest.raises(LexicalIndexStale):
        LexicalIndex(database).search(snapshot, "needle")


def test_sqlite_runtime_version_is_part_of_durable_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import local_dev.retrieval.index as index_module

    repo = tmp_path / "sqlite-revision"
    repo.mkdir()
    (repo / "a.txt").write_text("needle", encoding="utf-8")
    snapshot = RepositoryScanner().scan(repo.resolve())
    database = _db(tmp_path)
    LexicalIndex(database).sync(snapshot)

    monkeypatch.setattr(index_module.sqlite3, "sqlite_version", "different-runtime")
    with pytest.raises(LexicalIndexStale):
        LexicalIndex(database).search(snapshot, "needle")
