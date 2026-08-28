from __future__ import annotations

from typing import TYPE_CHECKING

from local_dev.repository.contracts import RepositorySnapshot
from local_dev.structure._index_common import nonempty, validated_read
from local_dev.structure._paths import encode_path, require_path
from local_dev.structure._storage_records import row_to_import, row_to_symbol
from local_dev.structure.contracts import (
    ImportKind,
    StructuralFileReport,
    StructuralImport,
    StructuralQueryError,
    StructuralSymbol,
    SymbolKind,
)

if TYPE_CHECKING:
    from local_dev.structure.index import StructuralIndex


def files(index: StructuralIndex, snapshot: RepositorySnapshot) -> tuple[StructuralFileReport, ...]:
    with validated_read(index, snapshot) as (_, _, states):
        return tuple(states[path].report() for path in sorted(states))


def symbols(
    index: StructuralIndex,
    snapshot: RepositorySnapshot,
    *,
    name: str | None,
    qualified_name: str | None,
    path: str | None,
    kind: SymbolKind | None,
    parent_qualified_name: str | None,
    limit: int | None,
) -> tuple[StructuralSymbol, ...]:
    clauses = ["repository_id = ?"]
    params: list[object] = []
    result_limit = index._limit(limit)
    if name is not None:
        clauses.append("name = ?")
        params.append(nonempty(name, "name"))
    if qualified_name is not None:
        clauses.append("qualified_name = ?")
        params.append(nonempty(qualified_name, "qualified_name"))
    if path is not None:
        clauses.append("path = ?")
        params.append(encode_path(require_path(path)))
    if kind is not None:
        if not isinstance(kind, SymbolKind):
            raise TypeError("kind must be SymbolKind")
        clauses.append("kind = ?")
        params.append(kind.value)
    if parent_qualified_name is not None:
        clauses.append("parent_qualified_name = ?")
        params.append(nonempty(parent_qualified_name, "parent_qualified_name"))
    with validated_read(index, snapshot) as (connection, repo_id, _):
        rows = connection.execute(
            "SELECT * FROM structural_symbols WHERE "
            + " AND ".join(clauses)
            + " ORDER BY path, start_line, start_col, symbol_id LIMIT ?",
            (repo_id, *params, result_limit + 1),
        ).fetchall()
        if len(rows) > result_limit:
            raise StructuralQueryError("symbol query exceeds result limit; refine filters")
        return tuple(row_to_symbol(row) for row in rows)


def imports(
    index: StructuralIndex,
    snapshot: RepositorySnapshot,
    *,
    module: str | None,
    name: str | None,
    path: str | None,
    kind: ImportKind | None,
    scope_qualified_name: str | None,
    limit: int | None,
) -> tuple[StructuralImport, ...]:
    clauses = ["repository_id = ?"]
    params: list[object] = []
    result_limit = index._limit(limit)
    if module is not None:
        clauses.append("module = ?")
        params.append(nonempty(module, "module"))
    if name is not None:
        clauses.append("name = ?")
        params.append(nonempty(name, "name"))
    if path is not None:
        clauses.append("path = ?")
        params.append(encode_path(require_path(path)))
    if kind is not None:
        if not isinstance(kind, ImportKind):
            raise TypeError("kind must be ImportKind")
        clauses.append("kind = ?")
        params.append(kind.value)
    if scope_qualified_name is not None:
        clauses.append("scope_qualified_name = ?")
        params.append(nonempty(scope_qualified_name, "scope_qualified_name"))
    with validated_read(index, snapshot) as (connection, repo_id, _):
        rows = connection.execute(
            "SELECT * FROM structural_imports WHERE "
            + " AND ".join(clauses)
            + " ORDER BY path, line, col, ordinal, import_id LIMIT ?",
            (repo_id, *params, result_limit + 1),
        ).fetchall()
        if len(rows) > result_limit:
            raise StructuralQueryError("import query exceeds result limit; refine filters")
        return tuple(row_to_import(row) for row in rows)
