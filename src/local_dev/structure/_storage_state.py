from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from local_dev.structure._contract_helpers import require_digest
from local_dev.structure._paths import decode_path
from local_dev.structure.contracts import StructuralIndexError


@dataclass(frozen=True, slots=True)
class IndexState:
    snapshot_fingerprint: str
    policy_fingerprint: str
    structure_digest: str
    file_count: int
    symbol_count: int
    import_count: int


def load_state(connection: sqlite3.Connection, repository_id: str) -> IndexState | None:
    row = connection.execute(
        """
        SELECT snapshot_fingerprint, policy_fingerprint, structure_digest,
               file_count, symbol_count, import_count
        FROM structural_index_state
        WHERE repository_id = ?
        """,
        (repository_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        snapshot = require_digest(str(row["snapshot_fingerprint"]), "snapshot_fingerprint")
        policy = require_digest(str(row["policy_fingerprint"]), "policy_fingerprint")
        digest = require_digest(str(row["structure_digest"]), "structure_digest")
        file_count = int(row["file_count"])
        symbol_count = int(row["symbol_count"])
        import_count = int(row["import_count"])
        if min(file_count, symbol_count, import_count) < 0:
            raise ValueError("durable counts must be non-negative")
        return IndexState(snapshot, policy, digest, file_count, symbol_count, import_count)
    except (TypeError, ValueError, KeyError) as exc:
        raise StructuralIndexError("durable structural index state is malformed") from exc


def counts(connection: sqlite3.Connection, repository_id: str) -> tuple[int, int, int]:
    file_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM structural_files WHERE repository_id = ?",
            (repository_id,),
        ).fetchone()[0]
    )
    symbol_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM structural_symbols WHERE repository_id = ?",
            (repository_id,),
        ).fetchone()[0]
    )
    import_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM structural_imports WHERE repository_id = ?",
            (repository_id,),
        ).fetchone()[0]
    )
    return file_count, symbol_count, import_count


def compute_digest(connection: sqlite3.Connection, repository_id: str) -> str:
    hasher = hashlib.sha256()
    queries = (
        (
            "files",
            """SELECT path, file_sha256, size_bytes, language, content_kind, status,
                      error_message, symbol_count, import_count
               FROM structural_files WHERE repository_id = ? ORDER BY path""",
        ),
        (
            "symbols",
            """SELECT path, symbol_id, file_sha256, kind, name, qualified_name,
                      parent_symbol_id, parent_qualified_name, start_line, start_col,
                      end_line, end_col, signature, decorators_json
               FROM structural_symbols WHERE repository_id = ?
               ORDER BY path, start_line, start_col, symbol_id""",
        ),
        (
            "imports",
            """SELECT path, import_id, file_sha256, kind, module, name, alias,
                      level, scope_symbol_id, scope_qualified_name, line, col, ordinal
               FROM structural_imports WHERE repository_id = ?
               ORDER BY path, line, col, ordinal, import_id""",
        ),
    )
    try:
        for label, query in queries:
            hasher.update(label.encode("utf-8"))
            hasher.update(b"\n")
            for row in connection.execute(query, (repository_id,)):
                values = [row[index] for index in range(len(row))]
                if values:
                    values[0] = decode_path(str(values[0]))
                encoded = json.dumps(
                    values,
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                hasher.update(encoded)
                hasher.update(b"\n")
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise StructuralIndexError("durable structural index payload is malformed") from exc
    return hasher.hexdigest()


def validate_state_counts(
    connection: sqlite3.Connection,
    repository_id: str,
    state: IndexState,
) -> None:
    if counts(connection, repository_id) != (
        state.file_count,
        state.symbol_count,
        state.import_count,
    ):
        raise StructuralIndexError("durable structural index counts are inconsistent")
    if compute_digest(connection, repository_id) != state.structure_digest:
        raise StructuralIndexError("durable structural digest is inconsistent")
