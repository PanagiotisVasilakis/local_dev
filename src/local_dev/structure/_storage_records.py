from __future__ import annotations

import json
import sqlite3

from local_dev.structure._paths import decode_path
from local_dev.structure.contracts import (
    ImportKind,
    StructuralImport,
    StructuralIndexError,
    StructuralSymbol,
    SymbolKind,
)


def row_to_symbol(row: sqlite3.Row) -> StructuralSymbol:
    try:
        decoded = json.loads(str(row["decorators_json"]))
        if not isinstance(decoded, list):
            raise ValueError("decorators_json must decode to a list")
        decorators = tuple(decoded)
        return StructuralSymbol(
            symbol_id=str(row["symbol_id"]),
            path=decode_path(str(row["path"])),
            file_sha256=str(row["file_sha256"]),
            kind=SymbolKind(str(row["kind"])),
            name=str(row["name"]),
            qualified_name=str(row["qualified_name"]),
            parent_symbol_id=(
                None if row["parent_symbol_id"] is None else str(row["parent_symbol_id"])
            ),
            parent_qualified_name=(
                None
                if row["parent_qualified_name"] is None
                else str(row["parent_qualified_name"])
            ),
            start_line=int(row["start_line"]),
            start_col=int(row["start_col"]),
            end_line=int(row["end_line"]),
            end_col=int(row["end_col"]),
            signature=None if row["signature"] is None else str(row["signature"]),
            decorators=decorators,
        )
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise StructuralIndexError("durable structural symbol row is malformed") from exc


def row_to_import(row: sqlite3.Row) -> StructuralImport:
    try:
        return StructuralImport(
            import_id=str(row["import_id"]),
            path=decode_path(str(row["path"])),
            file_sha256=str(row["file_sha256"]),
            kind=ImportKind(str(row["kind"])),
            module=None if row["module"] is None else str(row["module"]),
            name=str(row["name"]),
            alias=None if row["alias"] is None else str(row["alias"]),
            level=int(row["level"]),
            scope_symbol_id=(
                None if row["scope_symbol_id"] is None else str(row["scope_symbol_id"])
            ),
            scope_qualified_name=(
                None
                if row["scope_qualified_name"] is None
                else str(row["scope_qualified_name"])
            ),
            line=int(row["line"]),
            col=int(row["col"]),
            ordinal=int(row["ordinal"]),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise StructuralIndexError("durable structural import row is malformed") from exc
