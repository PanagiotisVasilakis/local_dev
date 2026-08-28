from __future__ import annotations

import hashlib
from pathlib import Path

from local_dev.db import Database
from local_dev.repository.contracts import (
    RepositoryContentKind,
    RepositoryEntry,
    RepositoryEntryKind,
    RepositorySnapshot,
    repository_fingerprint,
)
from local_dev.structure import ImportKind, StructuralFileStatus, StructuralIndex, SymbolKind


def _snapshot(root: Path) -> RepositorySnapshot:
    entries: list[RepositoryEntry] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        entries.append(
            RepositoryEntry(
                path=rel,
                kind=RepositoryEntryKind.FILE,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                executable=False,
                content_kind=(
                    RepositoryContentKind.EMPTY if not data else RepositoryContentKind.TEXT
                ),
                language="Python" if rel.endswith(".py") else None,
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


def test_indexes_definitions_variables_and_imports(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(
        "import os\n"
        "from pkg import item as alias\n"
        "VALUE = 1\n"
        "class C:\n"
        "    X = 2\n"
        "    def m(self, value: int = 1) -> str:\n"
        "        return str(value)\n"
        "def f():\n"
        "    local = 3\n"
        "    return local\n"
    )
    snap = _snapshot(repo)
    index = StructuralIndex(_db(tmp_path))
    result = index.sync(snap)
    assert result.symbol_count == 5
    assert [item.qualified_name for item in index.symbols(snap, limit=20)] == [
        "VALUE",
        "C",
        "C.X",
        "C.m",
        "f",
    ]
    assert index.symbols(snap, qualified_name="C.m")[0].signature == (
        "m(self, value: int = 1) -> str"
    )
    assert [(item.module, item.name) for item in index.imports(snap, limit=20)] == [
        ("os", "os"),
        ("pkg", "item"),
    ]


def test_nested_scopes_and_async_methods(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(
        "class A:\n"
        "    async def m(self):\n"
        "        import asyncio\n"
        "        def inner():\n"
        "            return 1\n"
        "        return inner\n"
    )
    snap = _snapshot(repo)
    index = StructuralIndex(_db(tmp_path))
    index.sync(snap)
    assert [(item.kind, item.qualified_name) for item in index.symbols(snap, limit=20)] == [
        (SymbolKind.CLASS, "A"),
        (SymbolKind.ASYNC_METHOD, "A.m"),
        (SymbolKind.FUNCTION, "A.m.inner"),
    ]
    assert index.imports(snap, module="asyncio")[0].scope_qualified_name == "A.m"


def test_signature_decorator_and_positional_only_arguments(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(
        "@decorate(1)\n"
        "def f(a: int, /, b=2, *args: str, c=3, **kwargs: object) -> bool:\n"
        "    return True\n"
    )
    snap = _snapshot(repo)
    index = StructuralIndex(_db(tmp_path))
    index.sync(snap)
    symbol = index.symbols(snap, name="f")[0]
    assert symbol.decorators == ("decorate(1)",)
    assert symbol.signature == (
        "f(a: int, /, b = 2, *args: str, c = 3, **kwargs: object) -> bool"
    )


def test_type_aliases_and_pep695_parameters(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(
        "type Vec[T] = list[T]\n"
        "class Box[T]:\n"
        "    def get[U](self, value: U) -> U:\n"
        "        type Local[V] = tuple[U, V]\n"
        "        return value\n"
    )
    snap = _snapshot(repo)
    index = StructuralIndex(_db(tmp_path))
    index.sync(snap)
    aliases = index.symbols(snap, kind=SymbolKind.TYPE_ALIAS, limit=20)
    assert [item.qualified_name for item in aliases] == ["Vec", "Box.get.Local"]
    assert index.symbols(snap, name="Box")[0].signature == "Box[T]"
    assert index.symbols(snap, name="get")[0].signature == "get[U](self, value: U) -> U"


def test_relative_and_dotted_import_semantics(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("import a.b as c\nfrom . import x\nfrom ..pkg import y\n")
    snap = _snapshot(repo)
    index = StructuralIndex(_db(tmp_path))
    index.sync(snap)
    rows = index.imports(snap, limit=20)
    assert [(row.kind, row.module, row.name, row.level) for row in rows] == [
        (ImportKind.IMPORT, "a.b", "a.b", 0),
        (ImportKind.FROM_IMPORT, None, "x", 1),
        (ImportKind.FROM_IMPORT, "pkg", "y", 2),
    ]


def test_compile_invalid_python_is_parse_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("return 1\n")
    snap = _snapshot(repo)
    index = StructuralIndex(_db(tmp_path))
    result = index.sync(snap)
    assert result.reports[0].status is StructuralFileStatus.PARSE_ERROR
    assert not index.symbols(snap)


def test_pep263_source_encoding(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_bytes(
        "# -*- coding: latin-1 -*-\nname = 'café'\ndef f(): pass\n".encode("latin-1")
    )
    snap = _snapshot(repo)
    index = StructuralIndex(_db(tmp_path))
    index.sync(snap)
    assert index.symbols(snap, name="f")


def test_duplicate_definitions_have_distinct_ids(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f(): pass\ndef f(): pass\n")
    snap = _snapshot(repo)
    index = StructuralIndex(_db(tmp_path))
    index.sync(snap)
    symbols = index.symbols(snap, name="f", limit=10)
    assert len(symbols) == 2
    assert len({item.symbol_id for item in symbols}) == 2


def test_destructuring_uses_target_positions(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("a, b = 1, 2\n")
    snap = _snapshot(repo)
    index = StructuralIndex(_db(tmp_path))
    index.sync(snap)
    a, b = index.symbols(snap, limit=10)
    assert a.name == "a" and b.name == "b"
    assert a.start_col < b.start_col


def test_nested_class_inside_function_has_methods(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(
        "def outer():\n"
        "    class Inner:\n"
        "        def method(self):\n"
        "            def nested(): pass\n"
        "            return nested\n"
        "    return Inner\n"
    )
    snap = _snapshot(repo)
    index = StructuralIndex(_db(tmp_path))
    index.sync(snap)
    rows = index.symbols(snap, limit=20)
    assert [(row.kind, row.qualified_name) for row in rows] == [
        (SymbolKind.FUNCTION, "outer"),
        (SymbolKind.CLASS, "outer.Inner"),
        (SymbolKind.METHOD, "outer.Inner.method"),
        (SymbolKind.FUNCTION, "outer.Inner.method.nested"),
    ]


def test_class_signature_includes_bases_keywords_and_type_params(tmp_path: Path) -> None:
    repo = tmp_path / "repo_class_signature"
    repo.mkdir()
    (repo / "a.py").write_text(
        "class Box[T](Base[int], metaclass=Meta):\n"
        "    pass\n"
    )
    snapshot = _snapshot(repo)
    index = StructuralIndex(_db(tmp_path / "class_signature"))
    index.sync(snapshot)
    symbol = index.symbols(snapshot, qualified_name="Box")[0]
    assert symbol.signature == "Box[T](Base[int], metaclass=Meta)"
