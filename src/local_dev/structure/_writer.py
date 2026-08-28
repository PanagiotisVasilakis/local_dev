from __future__ import annotations

import json
import sqlite3

from local_dev.repository.contracts import (
    RepositoryContentKind,
    RepositoryEntry,
    RepositorySnapshot,
)
from local_dev.repository.reader import read_snapshot_file
from local_dev.structure._parser import parse_python
from local_dev.structure._paths import encode_path
from local_dev.structure.contracts import StructuralFileStatus, StructuralIndexPolicy


def index_entry(
    connection: sqlite3.Connection,
    repository_id: str,
    snapshot: RepositorySnapshot,
    entry: RepositoryEntry,
    policy: StructuralIndexPolicy,
) -> None:
    status: StructuralFileStatus
    error_message: str | None = None
    symbols = ()
    imports = ()
    if entry.language != "Python":
        status = StructuralFileStatus.UNSUPPORTED_LANGUAGE
    elif entry.content_kind not in {RepositoryContentKind.TEXT, RepositoryContentKind.EMPTY}:
        status = StructuralFileStatus.UNSUPPORTED_CONTENT
    elif entry.size_bytes > policy.max_file_bytes:
        status = StructuralFileStatus.SKIPPED_SIZE
    else:
        payload = read_snapshot_file(snapshot, entry.path, max_bytes=policy.max_file_bytes)
        try:
            parsed = parse_python(payload, entry.path, entry.sha256, policy)
        except SyntaxError as exc:
            status = StructuralFileStatus.PARSE_ERROR
            error_message = _syntax_message(exc)
        else:
            status = StructuralFileStatus.INDEXED
            symbols = parsed.symbols
            imports = parsed.imports

    key = encode_path(entry.path)
    connection.execute(
        """
        INSERT INTO structural_files(
            repository_id, path, file_sha256, size_bytes, language, content_kind,
            status, error_message, symbol_count, import_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            repository_id,
            key,
            entry.sha256,
            entry.size_bytes,
            entry.language,
            entry.content_kind.value,
            status.value,
            error_message,
            len(symbols),
            len(imports),
        ),
    )
    for symbol in symbols:
        connection.execute(
            """
            INSERT INTO structural_symbols(
                repository_id, path, symbol_id, file_sha256, kind, name, qualified_name,
                parent_symbol_id, parent_qualified_name, start_line, start_col, end_line,
                end_col, signature, decorators_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repository_id,
                key,
                symbol.symbol_id,
                symbol.file_sha256,
                symbol.kind.value,
                symbol.name,
                symbol.qualified_name,
                symbol.parent_symbol_id,
                symbol.parent_qualified_name,
                symbol.start_line,
                symbol.start_col,
                symbol.end_line,
                symbol.end_col,
                symbol.signature,
                json.dumps(symbol.decorators, ensure_ascii=True, separators=(",", ":")),
            ),
        )
    for imported in imports:
        connection.execute(
            """
            INSERT INTO structural_imports(
                repository_id, path, import_id, file_sha256, kind, module, name, alias,
                level, scope_symbol_id, scope_qualified_name, line, col, ordinal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repository_id,
                key,
                imported.import_id,
                imported.file_sha256,
                imported.kind.value,
                imported.module,
                imported.name,
                imported.alias,
                imported.level,
                imported.scope_symbol_id,
                imported.scope_qualified_name,
                imported.line,
                imported.col,
                imported.ordinal,
            ),
        )


def _syntax_message(exc: SyntaxError) -> str:
    message = str(exc).replace("\x00", "\\0")
    message = message.encode("utf-8", errors="backslashreplace").decode("utf-8")
    return message[:1000] or "Python source is not compile-valid"
