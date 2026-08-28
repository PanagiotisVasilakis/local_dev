from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from local_dev.db import Database
from local_dev.repository.contracts import (
    RepositoryContentKind,
    RepositoryEntry,
    RepositoryEntryKind,
    RepositoryScanRaceError,
    RepositorySnapshot,
    repository_fingerprint,
)
from local_dev.structure import (
    StructuralFileStatus,
    StructuralIndex,
    StructuralIndexError,
    StructuralIndexPolicy,
    StructuralIndexStale,
)


def _snapshot(root: Path) -> RepositorySnapshot:
    entries: list[RepositoryEntry] = []
    candidates = (
        item for item in root.rglob("*") if item.is_file() and not item.is_symlink()
    )
    for path in sorted(candidates):
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        entries.append(
            RepositoryEntry(
                rel,
                RepositoryEntryKind.FILE,
                len(data),
                hashlib.sha256(data).hexdigest(),
                False,
                RepositoryContentKind.EMPTY if not data else RepositoryContentKind.TEXT,
                "Python" if rel.endswith(".py") else None,
            )
        )
    ordered = tuple(entries)
    return RepositorySnapshot(root.resolve(), ordered, repository_fingerprint(ordered))


def _db(tmp_path: Path) -> Database:
    database = Database(
        tmp_path / "state.db",
        Path(__file__).parents[1] / "src/local_dev/migrations",
    )
    database.migrate()
    return database


def test_incremental_sync_and_stale_snapshot(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "a.py"
    target.write_text("a = 1\n")
    index = StructuralIndex(_db(tmp_path))
    first = _snapshot(repo)
    assert index.sync(first).rebuilt_paths == ("a.py",)
    assert index.sync(first).unchanged_paths == ("a.py",)
    target.write_text("b = 2\n")
    second = _snapshot(repo)
    assert index.sync(second).rebuilt_paths == ("a.py",)
    with pytest.raises(StructuralIndexStale):
        index.symbols(first)


def test_policy_change_rebuilds_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("a = 1\n")
    database = _db(tmp_path)
    snap = _snapshot(repo)
    StructuralIndex(database).sync(snap)
    result = StructuralIndex(database, StructuralIndexPolicy(default_limit=50)).sync(snap)
    assert result.rebuilt_paths == ("a.py",)


def test_resource_limit_failure_rolls_back(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "a.py"
    target.write_text("a = 1\n")
    database = _db(tmp_path)
    index = StructuralIndex(database, StructuralIndexPolicy(max_symbols_per_file=2))
    first = _snapshot(repo)
    index.sync(first)
    target.write_text("a = 1\nb = 2\nc = 3\n")
    with pytest.raises(StructuralIndexError):
        index.sync(_snapshot(repo))
    assert index.symbols(first, name="a")


def test_snapshot_change_during_read_rolls_back(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "a.py"
    target.write_text("old = 1\n")
    index = StructuralIndex(_db(tmp_path))
    old = _snapshot(repo)
    index.sync(old)
    target.write_text("new = 1\n")
    intended = _snapshot(repo)
    target.write_text("changed_again = 1\n")
    with pytest.raises(RepositoryScanRaceError):
        index.sync(intended)
    assert index.symbols(old, name="old")


def test_symlink_replacement_fails_closed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("bad = 1\n")
    target = repo / "a.py"
    target.write_text("safe = 1\n")
    snap = _snapshot(repo)
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(RepositoryScanRaceError):
        StructuralIndex(_db(tmp_path)).sync(snap)


def test_concurrent_sync_serializes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("a = 1\n")
    database = _db(tmp_path)
    snap = _snapshot(repo)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: StructuralIndex(database).sync(snap), range(2)))
    assert all(result.symbol_count == 1 for result in results)


def test_coverage_reports_unsupported_and_oversized(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n" * 20)
    (repo / "notes.txt").write_text("hello")
    snap = _snapshot(repo)
    result = StructuralIndex(
        _db(tmp_path),
        StructuralIndexPolicy(max_file_bytes=10),
    ).sync(snap)
    reports = {item.path: item.status for item in result.reports}
    assert reports == {
        "a.py": StructuralFileStatus.SKIPPED_SIZE,
        "notes.txt": StructuralFileStatus.UNSUPPORTED_LANGUAGE,
    }


def test_multi_repo_isolation(tmp_path: Path) -> None:
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    (one / "a.py").write_text("x = 1\n")
    (two / "a.py").write_text("x = 1\n")
    database = _db(tmp_path)
    index = StructuralIndex(database)
    first = _snapshot(one)
    second = _snapshot(two)
    index.sync(first)
    index.sync(second)
    assert index.symbols(first, name="x")
    assert index.symbols(second, name="x")


def test_import_limit_failure_rolls_back(tmp_path: Path) -> None:
    repo = tmp_path / "repo_import_limit"
    repo.mkdir()
    target = repo / "a.py"
    target.write_text("import os\n")
    database = _db(tmp_path / "import_limit")
    index = StructuralIndex(database, StructuralIndexPolicy(max_imports_per_file=1))
    first = _snapshot(repo)
    index.sync(first)

    target.write_text("import os\nimport sys\n")
    with pytest.raises(StructuralIndexError):
        index.sync(_snapshot(repo))

    imports = index.imports(first)
    assert [(item.module, item.name) for item in imports] == [("os", "os")]


def test_ast_node_limit_failure_rolls_back(tmp_path: Path) -> None:
    repo = tmp_path / "repo_ast_limit"
    repo.mkdir()
    target = repo / "a.py"
    target.write_text("a = 1\n")
    database = _db(tmp_path / "ast_limit")
    index = StructuralIndex(database, StructuralIndexPolicy(max_ast_nodes=10))
    first = _snapshot(repo)
    index.sync(first)

    target.write_text("\n".join(f"name_{index} = {index}" for index in range(8)) + "\n")
    with pytest.raises(StructuralIndexError):
        index.sync(_snapshot(repo))

    assert [item.qualified_name for item in index.symbols(first)] == ["a"]
