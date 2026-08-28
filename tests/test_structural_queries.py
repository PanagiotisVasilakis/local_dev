from __future__ import annotations

import hashlib
import os
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
from local_dev.structure import (
    ImportKind,
    StructuralFileReport,
    StructuralFileStatus,
    StructuralImport,
    StructuralIndex,
    StructuralIndexPolicy,
    StructuralQueryError,
    StructuralSymbol,
    StructuralSyncResult,
    SymbolKind,
)


def _snapshot(root: Path, name: str = "a.py") -> RepositorySnapshot:
    data = (root / name).read_bytes()
    entry = RepositoryEntry(
        name,
        RepositoryEntryKind.FILE,
        len(data),
        hashlib.sha256(data).hexdigest(),
        False,
        RepositoryContentKind.EMPTY if not data else RepositoryContentKind.TEXT,
        "Python",
    )
    return RepositorySnapshot(root.resolve(), (entry,), repository_fingerprint((entry,)))


def _db(tmp_path: Path) -> Database:
    database = Database(
        tmp_path / "state.db",
        Path(__file__).parents[1] / "src/local_dev/migrations",
    )
    database.migrate()
    return database


def test_symbol_and_import_filters(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("import os\nclass A:\n    def m(self): pass\n")
    snap = _snapshot(repo)
    index = StructuralIndex(_db(tmp_path))
    index.sync(snap)
    method = index.symbols(
        snap,
        path="a.py",
        kind=SymbolKind.METHOD,
        parent_qualified_name="A",
    )[0]
    assert method.qualified_name == "A.m"
    imported = index.imports(snap, module="os", kind=ImportKind.IMPORT)[0]
    assert imported.name == "os"


def test_broad_query_fails_closed_at_limit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("a = 1\nb = 2\nc = 3\n")
    snap = _snapshot(repo)
    index = StructuralIndex(
        _db(tmp_path),
        StructuralIndexPolicy(default_limit=2, max_results=2),
    )
    index.sync(snap)
    with pytest.raises(StructuralQueryError):
        index.symbols(snap)


def test_invalid_path_and_enum_are_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("a = 1\n")
    snap = _snapshot(repo)
    index = StructuralIndex(_db(tmp_path))
    index.sync(snap)
    with pytest.raises(ValueError):
        index.symbols(snap, path="../a.py")
    with pytest.raises(TypeError):
        index.symbols(snap, kind="variable")  # type: ignore[arg-type]


def test_non_utf8_posix_filename_round_trip(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX only")
    repo = tmp_path / "repo"
    repo.mkdir()
    raw = os.fsencode(repo) + b"/bad-\xff.py"
    descriptor = os.open(raw, os.O_CREAT | os.O_WRONLY, 0o644)
    os.write(descriptor, b"def f(): pass\n")
    os.close(descriptor)
    name = os.listdir(repo)[0]
    snap = _snapshot(repo, name)
    index = StructuralIndex(_db(tmp_path))
    index.sync(snap)
    assert index.symbols(snap, path=name)[0].name == "f"


def test_non_utf8_filename_parse_error_is_sqlite_safe(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX only")
    repo = tmp_path / "repo"
    repo.mkdir()
    raw = os.fsencode(repo) + b"/bad-\xff.py"
    descriptor = os.open(raw, os.O_CREAT | os.O_WRONLY, 0o644)
    os.write(descriptor, b"def broken(:\n")
    os.close(descriptor)
    name = os.listdir(repo)[0]
    snap = _snapshot(repo, name)
    result = StructuralIndex(_db(tmp_path)).sync(snap)
    assert result.reports[0].status is StructuralFileStatus.PARSE_ERROR
    assert result.reports[0].error_message


def test_contracts_reject_invalid_runtime_values() -> None:
    with pytest.raises(TypeError):
        StructuralFileReport("a.py", "indexed", 0, 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        StructuralSymbol(
            "a" * 64,
            "a.py",
            "b" * 64,
            SymbolKind.VARIABLE,
            "x",
            "x",
            None,
            None,
            2,
            5,
            2,
            4,
        )
    with pytest.raises(ValueError):
        StructuralImport(
            "a" * 64,
            "a.py",
            "b" * 64,
            ImportKind.IMPORT,
            None,
            "os",
            None,
            0,
            None,
            None,
            1,
            0,
            0,
        )


def test_sync_result_requires_coherent_path_partition() -> None:
    report = StructuralFileReport("a.py", StructuralFileStatus.INDEXED, 0, 0)
    with pytest.raises(ValueError):
        StructuralSyncResult(
            "a" * 64,
            ("a.py",),
            (),
            ("a.py",),
            (report,),
            0,
            0,
        )


def test_empty_python_file_is_indexed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_bytes(b"")
    snap = _snapshot(repo)
    result = StructuralIndex(_db(tmp_path)).sync(snap)
    assert result.reports[0].status is StructuralFileStatus.INDEXED


def test_escape_prefix_filename_round_trip(tmp_path: Path) -> None:
    repo = tmp_path / "repo_escape_prefix"
    repo.mkdir()
    name = "\x1fmodule.py"
    (repo / name).write_text("needle = 1\n")
    snapshot = _snapshot(repo, name)
    index = StructuralIndex(_db(tmp_path / "escape_prefix"))
    index.sync(snapshot)
    assert [item.path for item in index.symbols(snapshot, path=name)] == [name]
