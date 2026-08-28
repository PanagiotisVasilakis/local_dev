from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from local_dev.repository.contracts import RepositoryContentKind, RepositoryEntry
from local_dev.structure._paths import decode_path, encode_path
from local_dev.structure.contracts import (
    StructuralFileReport,
    StructuralFileStatus,
    StructuralIndexError,
    StructuralIndexPolicy,
)


@dataclass(frozen=True, slots=True)
class FileState:
    path: str
    file_sha256: str
    size_bytes: int
    language: str | None
    content_kind: str
    status: StructuralFileStatus
    error_message: str | None
    symbol_count: int
    import_count: int

    def report(self) -> StructuralFileReport:
        return StructuralFileReport(
            path=self.path,
            status=self.status,
            symbol_count=self.symbol_count,
            import_count=self.import_count,
            error_message=self.error_message,
        )


def load_files(connection: sqlite3.Connection, repository_id: str) -> dict[str, FileState]:
    rows = connection.execute(
        """
        SELECT path, file_sha256, size_bytes, language, content_kind, status,
               error_message, symbol_count, import_count
        FROM structural_files
        WHERE repository_id = ?
        ORDER BY path
        """,
        (repository_id,),
    ).fetchall()
    result: dict[str, FileState] = {}
    try:
        for row in rows:
            path = decode_path(str(row["path"]))
            state = FileState(
                path=path,
                file_sha256=str(row["file_sha256"]),
                size_bytes=int(row["size_bytes"]),
                language=None if row["language"] is None else str(row["language"]),
                content_kind=str(row["content_kind"]),
                status=StructuralFileStatus(str(row["status"])),
                error_message=(
                    None if row["error_message"] is None else str(row["error_message"])
                ),
                symbol_count=int(row["symbol_count"]),
                import_count=int(row["import_count"]),
            )
            if path in result:
                raise StructuralIndexError("durable structural paths are not unique")
            result[path] = state
    except (TypeError, ValueError, KeyError) as exc:
        raise StructuralIndexError("durable structural file metadata is malformed") from exc
    return result


def expected_status(
    entry: RepositoryEntry,
    policy: StructuralIndexPolicy,
) -> tuple[StructuralFileStatus, ...]:
    if entry.language != "Python":
        return (StructuralFileStatus.UNSUPPORTED_LANGUAGE,)
    if entry.content_kind not in {RepositoryContentKind.TEXT, RepositoryContentKind.EMPTY}:
        return (StructuralFileStatus.UNSUPPORTED_CONTENT,)
    if entry.size_bytes > policy.max_file_bytes:
        return (StructuralFileStatus.SKIPPED_SIZE,)
    return (StructuralFileStatus.INDEXED, StructuralFileStatus.PARSE_ERROR)


def file_matches_entry(
    state: FileState | None,
    entry: RepositoryEntry,
    policy: StructuralIndexPolicy,
) -> bool:
    return bool(
        state is not None
        and state.file_sha256 == entry.sha256
        and state.size_bytes == entry.size_bytes
        and state.language == entry.language
        and state.content_kind == entry.content_kind.value
        and state.status in expected_status(entry, policy)
    )


def validate_file_set(
    files: dict[str, FileState],
    desired: dict[str, RepositoryEntry],
    policy: StructuralIndexPolicy,
) -> None:
    if set(files) != set(desired):
        raise StructuralIndexError("durable structural file set does not match snapshot")
    for path, entry in desired.items():
        state = files[path]
        if not file_matches_entry(state, entry, policy):
            raise StructuralIndexError(f"durable structural metadata mismatch: {path}")
        if state.status is StructuralFileStatus.INDEXED and state.error_message is not None:
            raise StructuralIndexError(f"indexed file has an error message: {path}")
        if state.status is StructuralFileStatus.PARSE_ERROR and not state.error_message:
            raise StructuralIndexError(f"parse-error file lacks error message: {path}")


def validate_row_counts(
    connection: sqlite3.Connection,
    repository_id: str,
    files: dict[str, FileState],
) -> None:
    symbol_rows = connection.execute(
        "SELECT path, COUNT(*) AS n FROM structural_symbols "
        "WHERE repository_id = ? GROUP BY path",
        (repository_id,),
    ).fetchall()
    import_rows = connection.execute(
        "SELECT path, COUNT(*) AS n FROM structural_imports "
        "WHERE repository_id = ? GROUP BY path",
        (repository_id,),
    ).fetchall()
    try:
        symbols = {decode_path(str(row["path"])): int(row["n"]) for row in symbol_rows}
        imports = {decode_path(str(row["path"])): int(row["n"]) for row in import_rows}
    except (TypeError, ValueError, KeyError) as exc:
        raise StructuralIndexError("durable structural row paths are malformed") from exc
    for path, state in files.items():
        if symbols.pop(path, 0) != state.symbol_count:
            raise StructuralIndexError(f"structural symbol row count mismatch: {path}")
        if imports.pop(path, 0) != state.import_count:
            raise StructuralIndexError(f"structural import row count mismatch: {path}")
    if symbols or imports:
        raise StructuralIndexError("orphan structural rows detected")


def delete_file(connection: sqlite3.Connection, repository_id: str, path: str) -> None:
    key = encode_path(path)
    for table in ("structural_imports", "structural_symbols", "structural_files"):
        connection.execute(
            f"DELETE FROM {table} WHERE repository_id=? AND path=?",
            (repository_id, key),
        )
