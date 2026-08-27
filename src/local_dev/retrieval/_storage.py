from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from local_dev.repository.contracts import RepositoryEntry
from local_dev.retrieval.contracts import (
    LexicalFileStatus,
    LexicalIndexError,
    LexicalIndexPolicy,
)


@dataclass(frozen=True, slots=True)
class IndexState:
    snapshot_fingerprint: str
    policy_fingerprint: str
    fts_digest: str
    indexed_file_count: int
    skipped_file_count: int
    lossy_file_count: int
    chunk_count: int


@dataclass(frozen=True, slots=True)
class FileState:
    path: str
    file_sha256: str
    size_bytes: int
    language: str | None
    content_kind: str
    status: LexicalFileStatus
    decode_lossy: bool
    chunk_count: int


def load_state(
    connection: sqlite3.Connection,
    repository_id: str,
) -> IndexState | None:
    row = connection.execute(
        """
        SELECT snapshot_fingerprint, policy_fingerprint, fts_digest,
               indexed_file_count, skipped_file_count,
               lossy_file_count, chunk_count
        FROM lexical_index_state
        WHERE repository_id = ?
        """,
        (repository_id,),
    ).fetchone()
    if row is None:
        return None
    return IndexState(
        snapshot_fingerprint=str(row["snapshot_fingerprint"]),
        policy_fingerprint=str(row["policy_fingerprint"]),
        fts_digest=str(row["fts_digest"]),
        indexed_file_count=int(row["indexed_file_count"]),
        skipped_file_count=int(row["skipped_file_count"]),
        lossy_file_count=int(row["lossy_file_count"]),
        chunk_count=int(row["chunk_count"]),
    )


def load_files(
    connection: sqlite3.Connection,
    repository_id: str,
) -> dict[str, FileState]:
    rows = connection.execute(
        """
        SELECT path, file_sha256, size_bytes, language, content_kind,
               status, decode_lossy, chunk_count
        FROM lexical_files
        WHERE repository_id = ?
        ORDER BY path
        """,
        (repository_id,),
    ).fetchall()
    result: dict[str, FileState] = {}
    for row in rows:
        path = decode_path(str(row["path"]))
        if path in result:
            raise LexicalIndexError("durable lexical paths are not uniquely encoded")
        result[path] = FileState(
            path=path,
            file_sha256=str(row["file_sha256"]),
            size_bytes=int(row["size_bytes"]),
            language=None if row["language"] is None else str(row["language"]),
            content_kind=str(row["content_kind"]),
            status=LexicalFileStatus(str(row["status"])),
            decode_lossy=bool(row["decode_lossy"]),
            chunk_count=int(row["chunk_count"]),
        )
    return result


def file_state_matches_entry(
    state: FileState | None,
    entry: RepositoryEntry,
    policy: LexicalIndexPolicy,
) -> bool:
    if state is None:
        return False
    expected_status = (
        LexicalFileStatus.SKIPPED_SIZE
        if entry.size_bytes > policy.max_file_bytes
        else LexicalFileStatus.INDEXED
    )
    return (
        state.file_sha256 == entry.sha256
        and state.size_bytes == entry.size_bytes
        and state.language == entry.language
        and state.content_kind == entry.content_kind.value
        and state.status is expected_status
    )


def delete_file(
    connection: sqlite3.Connection,
    repository_id: str,
    path: str,
) -> None:
    path_key = encode_path(path)
    connection.execute(
        "DELETE FROM lexical_chunks WHERE repository_id = ? AND path = ?",
        (repository_id, path_key),
    )
    connection.execute(
        "DELETE FROM lexical_files WHERE repository_id = ? AND path = ?",
        (repository_id, path_key),
    )


def delete_all_repository_files(
    connection: sqlite3.Connection,
    repository_id: str,
) -> None:
    connection.execute(
        "DELETE FROM lexical_chunks WHERE repository_id = ?",
        (repository_id,),
    )
    connection.execute(
        "DELETE FROM lexical_files WHERE repository_id = ?",
        (repository_id,),
    )


def validate_file_set(
    files: dict[str, FileState],
    desired: dict[str, RepositoryEntry],
    policy: LexicalIndexPolicy,
) -> None:
    if set(files) != set(desired):
        raise LexicalIndexError("durable lexical file set does not match repository snapshot")
    for path, entry in desired.items():
        if not file_state_matches_entry(files[path], entry, policy):
            raise LexicalIndexError(
                f"durable lexical metadata does not match repository snapshot: {path}"
            )


def validate_index_metadata(
    connection: sqlite3.Connection,
    repository_id: str,
    state: IndexState,
    files: dict[str, FileState],
    desired: dict[str, RepositoryEntry],
    policy: LexicalIndexPolicy,
) -> None:
    validate_file_set(files, desired, policy)
    validate_counts(connection, repository_id, state)


def validate_counts(
    connection: sqlite3.Connection,
    repository_id: str,
    state: IndexState,
) -> None:
    indexed, skipped, lossy, chunks = index_counts(connection, repository_id)
    if (
        indexed != state.indexed_file_count
        or skipped != state.skipped_file_count
        or lossy != state.lossy_file_count
        or chunks != state.chunk_count
    ):
        raise LexicalIndexError("durable lexical index counts are inconsistent")


def validate_chunk_counts(
    connection: sqlite3.Connection,
    repository_id: str,
    files: dict[str, FileState],
) -> None:
    rows = connection.execute(
        """
        SELECT path, COUNT(*) AS chunk_count
        FROM lexical_chunks
        WHERE repository_id = ?
        GROUP BY path
        """,
        (repository_id,),
    ).fetchall()
    actual = {
        decode_path(str(row["path"])): int(row["chunk_count"])
        for row in rows
    }
    for path, state in files.items():
        expected = state.chunk_count
        observed = actual.pop(path, 0)
        if observed != expected:
            raise LexicalIndexError(
                f"lexical chunk count is inconsistent for repository path: {path}"
            )
    if actual:
        raise LexicalIndexError(
            "lexical chunks exist without corresponding durable file metadata"
        )



def compute_fts_digest(
    connection: sqlite3.Connection,
    repository_id: str,
) -> str:
    rows = connection.execute(
        """
        SELECT c.chunk_id, v.col, v.offset, v.term
        FROM lexical_chunks_fts_vocab AS v
        JOIN lexical_chunks AS c
          ON c.chunk_rowid = v.doc
        WHERE c.repository_id = ?
        ORDER BY c.chunk_id, v.col, v.offset, v.term
        """,
        (repository_id,),
    )
    hasher = hashlib.sha256()
    for row in rows:
        encoded = json.dumps(
            (
                str(row["chunk_id"]),
                str(row["col"]),
                int(row["offset"]),
                str(row["term"]),
            ),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        hasher.update(encoded)
        hasher.update(b"\n")
    return hasher.hexdigest()


def validate_fts_digest(
    connection: sqlite3.Connection,
    repository_id: str,
    state: IndexState,
) -> None:
    orphan = connection.execute(
        """
        SELECT 1
        FROM lexical_chunks_fts_vocab AS v
        LEFT JOIN lexical_chunks AS c
          ON c.chunk_rowid = v.doc
        WHERE c.chunk_rowid IS NULL
        LIMIT 1
        """
    ).fetchone()
    if orphan is not None:
        raise LexicalIndexError(
            "FTS vocabulary contains terms without durable lexical chunks"
        )
    observed = compute_fts_digest(connection, repository_id)
    if observed != state.fts_digest:
        raise LexicalIndexError(
            "FTS vocabulary digest does not match durable lexical index state"
        )

def validate_fts_integrity(connection: sqlite3.Connection) -> None:
    try:
        connection.execute(
            """
            INSERT INTO lexical_chunks_fts(lexical_chunks_fts, rank)
            VALUES ('integrity-check', 1)
            """
        )
    except sqlite3.DatabaseError as exc:
        raise LexicalIndexError(
            "FTS index content is inconsistent with durable lexical chunks"
        ) from exc


def index_counts(
    connection: sqlite3.Connection,
    repository_id: str,
) -> tuple[int, int, int, int]:
    file_row = connection.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN status = 'indexed' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN status = 'skipped_size' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN decode_lossy = 1 THEN 1 ELSE 0 END), 0)
        FROM lexical_files
        WHERE repository_id = ?
        """,
        (repository_id,),
    ).fetchone()
    chunk_row = connection.execute(
        "SELECT COUNT(*) FROM lexical_chunks WHERE repository_id = ?",
        (repository_id,),
    ).fetchone()
    return (
        int(file_row[0]),
        int(file_row[1]),
        int(file_row[2]),
        int(chunk_row[0]),
    )


def coverage_paths(
    connection: sqlite3.Connection,
    repository_id: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    skipped_rows = connection.execute(
        """
        SELECT path FROM lexical_files
        WHERE repository_id = ? AND status = 'skipped_size'
        ORDER BY path
        """,
        (repository_id,),
    ).fetchall()
    lossy_rows = connection.execute(
        """
        SELECT path FROM lexical_files
        WHERE repository_id = ? AND decode_lossy = 1
        ORDER BY path
        """,
        (repository_id,),
    ).fetchall()
    return (
        tuple(sorted(decode_path(str(row["path"])) for row in skipped_rows)),
        tuple(sorted(decode_path(str(row["path"])) for row in lossy_rows)),
    )


_PATH_ESCAPE = "\x1f"


def encode_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError("lexical path must be a non-empty string")
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        payload = path.encode("utf-8", errors="surrogatepass").hex()
        return _PATH_ESCAPE + "s" + payload
    if path.startswith(_PATH_ESCAPE):
        return _PATH_ESCAPE + "u" + path
    return path


def decode_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise LexicalIndexError("durable lexical path encoding is invalid")
    if value.startswith(_PATH_ESCAPE + "s"):
        payload = value[2:]
        try:
            decoded = bytes.fromhex(payload).decode("utf-8", errors="surrogatepass")
        except (ValueError, UnicodeDecodeError) as exc:
            raise LexicalIndexError("durable lexical path encoding is invalid") from exc
        if not decoded:
            raise LexicalIndexError("durable lexical path encoding is invalid")
        return decoded
    if value.startswith(_PATH_ESCAPE + "u"):
        decoded = value[2:]
        if not decoded.startswith(_PATH_ESCAPE):
            raise LexicalIndexError("durable lexical path encoding is invalid")
        return decoded
    if value.startswith(_PATH_ESCAPE):
        raise LexicalIndexError("durable lexical path encoding is invalid")
    return value
