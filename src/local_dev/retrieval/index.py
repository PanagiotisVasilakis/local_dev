from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from local_dev.db import Database
from local_dev.repository.contracts import (
    RepositoryContentKind,
    RepositoryEntry,
    RepositoryEntryKind,
    RepositorySnapshot,
)
from local_dev.repository.reader import read_snapshot_file
from local_dev.retrieval._chunking import chunk_id, chunk_text, decode_text
from local_dev.retrieval._storage import (
    FileState,
    compute_fts_digest,
    coverage_paths,
    decode_path,
    delete_all_repository_files,
    delete_file,
    encode_path,
    file_state_matches_entry,
    index_counts,
    load_files,
    load_state,
    validate_chunk_counts,
    validate_counts,
    validate_file_set,
    validate_fts_digest,
    validate_fts_integrity,
    validate_index_metadata,
)
from local_dev.retrieval.contracts import (
    LexicalFileStatus,
    LexicalIndexError,
    LexicalIndexNotReady,
    LexicalIndexPolicy,
    LexicalIndexStale,
    LexicalQueryError,
    LexicalSearchHit,
    LexicalSearchResponse,
    LexicalSyncResult,
)

_QUERY_TOKEN = re.compile(r"\w+", re.UNICODE)
_INDEX_FORMAT_REVISION = 1


class LexicalIndex:
    """SQLite FTS5 lexical retrieval bound to deterministic repository snapshots."""

    def __init__(
        self,
        database: Database,
        policy: LexicalIndexPolicy | None = None,
    ) -> None:
        if not isinstance(database, Database):
            raise TypeError("database must be Database")
        if policy is not None and not isinstance(policy, LexicalIndexPolicy):
            raise TypeError("policy must be LexicalIndexPolicy")
        self._database = database
        self._policy = policy or LexicalIndexPolicy()
        self._policy_fingerprint = _policy_fingerprint(self._policy)

    def sync(self, snapshot: RepositorySnapshot) -> LexicalSyncResult:
        if not isinstance(snapshot, RepositorySnapshot):
            raise TypeError("snapshot must be RepositorySnapshot")

        repository_id = _repository_id(snapshot.repository_root)
        desired = _lexical_candidates(snapshot)

        try:
            with self._database.immediate_transaction() as connection:
                timestamp = datetime.now(UTC).isoformat()
                state = load_state(connection, repository_id)
                existing = load_files(connection, repository_id)

                if (
                    state is not None
                    and state.snapshot_fingerprint == snapshot.fingerprint_sha256
                    and state.policy_fingerprint == self._policy_fingerprint
                ):
                    validate_index_metadata(
                        connection,
                        repository_id,
                        state,
                        existing,
                        desired,
                        self._policy,
                    )
                    validate_chunk_counts(connection, repository_id, existing)
                    validate_fts_integrity(connection)
                    validate_fts_digest(connection, repository_id, state)
                    return _sync_result(
                        snapshot,
                        existing,
                        rebuilt=(),
                        removed=(),
                    )

                if state is None:
                    connection.execute(
                        """
                        INSERT INTO lexical_index_state(
                            repository_id, snapshot_fingerprint, policy_fingerprint,
                            fts_digest, indexed_file_count, skipped_file_count,
                            lossy_file_count, chunk_count, updated_at
                        ) VALUES (?, ?, ?, ?, 0, 0, 0, 0, ?)
                        """,
                        (
                            repository_id,
                            snapshot.fingerprint_sha256,
                            self._policy_fingerprint,
                            "0" * 64,
                            timestamp,
                        ),
                    )

                rebuild_all = (
                    state is None
                    or state.policy_fingerprint != self._policy_fingerprint
                )
                if rebuild_all:
                    removed_paths = tuple(sorted(set(existing) - set(desired)))
                    rebuild_paths = tuple(sorted(desired))
                    delete_all_repository_files(connection, repository_id)
                    existing = {}
                else:
                    removed_paths = tuple(sorted(set(existing) - set(desired)))
                    rebuild_paths = tuple(
                        sorted(
                            path
                            for path, entry in desired.items()
                            if not file_state_matches_entry(
                                existing.get(path),
                                entry,
                                self._policy,
                            )
                        )
                    )
                    for path in removed_paths:
                        delete_file(connection, repository_id, path)
                    for path in rebuild_paths:
                        if path in existing:
                            delete_file(connection, repository_id, path)

                for path in rebuild_paths:
                    _index_entry(
                        connection,
                        repository_id,
                        snapshot,
                        desired[path],
                        self._policy,
                    )

                current_files = load_files(connection, repository_id)
                validate_file_set(current_files, desired, self._policy)
                counts = index_counts(connection, repository_id)
                fts_digest = compute_fts_digest(connection, repository_id)
                connection.execute(
                    """
                    UPDATE lexical_index_state
                    SET snapshot_fingerprint = ?,
                        policy_fingerprint = ?,
                        fts_digest = ?,
                        indexed_file_count = ?,
                        skipped_file_count = ?,
                        lossy_file_count = ?,
                        chunk_count = ?,
                        updated_at = ?
                    WHERE repository_id = ?
                    """,
                    (
                        snapshot.fingerprint_sha256,
                        self._policy_fingerprint,
                        fts_digest,
                        counts[0],
                        counts[1],
                        counts[2],
                        counts[3],
                        timestamp,
                        repository_id,
                    ),
                )
                final_state = load_state(connection, repository_id)
                if final_state is None:
                    raise LexicalIndexError(
                        "lexical index state disappeared during synchronization"
                    )
                validate_index_metadata(
                    connection,
                    repository_id,
                    final_state,
                    current_files,
                    desired,
                    self._policy,
                )
                validate_chunk_counts(
                    connection,
                    repository_id,
                    current_files,
                )
                validate_fts_integrity(connection)

                unchanged = tuple(sorted(set(desired) - set(rebuild_paths)))
                return _sync_result(
                    snapshot,
                    current_files,
                    rebuilt=rebuild_paths,
                    removed=removed_paths,
                    unchanged=unchanged,
                )
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                raise LexicalIndexNotReady(
                    "lexical index schema is unavailable; run database migrations first"
                ) from exc
            raise LexicalIndexError("SQLite lexical synchronization failed") from exc
        except sqlite3.DatabaseError as exc:
            raise LexicalIndexError("SQLite lexical synchronization failed") from exc

    def search(
        self,
        snapshot: RepositorySnapshot,
        query: str,
        *,
        limit: int | None = None,
    ) -> LexicalSearchResponse:
        if not isinstance(snapshot, RepositorySnapshot):
            raise TypeError("snapshot must be RepositorySnapshot")
        result_limit = self._policy.default_limit if limit is None else limit
        if (
            not isinstance(result_limit, int)
            or isinstance(result_limit, bool)
            or result_limit <= 0
            or result_limit > self._policy.max_results
        ):
            raise ValueError(
                f"limit must be an integer between 1 and {self._policy.max_results}"
            )
        normalized_query, fts_query, score_terms = _prepare_query(query, self._policy)
        repository_id = _repository_id(snapshot.repository_root)

        try:
            with self._database.connect() as connection:
                state = load_state(connection, repository_id)
                if state is None:
                    raise LexicalIndexNotReady(
                        "no lexical index exists for the supplied repository"
                    )
                if (
                    state.snapshot_fingerprint != snapshot.fingerprint_sha256
                    or state.policy_fingerprint != self._policy_fingerprint
                ):
                    raise LexicalIndexStale(
                        "lexical index does not match the supplied repository snapshot"
                    )

                desired = _lexical_candidates(snapshot)
                files = load_files(connection, repository_id)
                validate_file_set(files, desired, self._policy)
                validate_counts(connection, repository_id, state)
                validate_chunk_counts(connection, repository_id, files)
                validate_fts_digest(connection, repository_id, state)

                rows = connection.execute(
                    """
                    SELECT
                        c.chunk_id,
                        c.path,
                        c.chunk_index,
                        c.start_line,
                        c.end_line,
                        c.content,
                        c.content_sha256,
                        f.language,
                        f.decode_lossy,
                        f.file_sha256
                    FROM lexical_chunks_fts
                    JOIN lexical_chunks AS c
                      ON c.chunk_rowid = lexical_chunks_fts.rowid
                    JOIN lexical_files AS f
                      ON f.repository_id = c.repository_id
                     AND f.path = c.path
                    WHERE lexical_chunks_fts MATCH ?
                      AND c.repository_id = ?
                    ORDER BY c.path ASC, c.chunk_index ASC
                    LIMIT ?
                    """,
                    (
                        fts_query,
                        repository_id,
                        self._policy.max_candidates + 1,
                    ),
                ).fetchall()
                if len(rows) > self._policy.max_candidates:
                    raise LexicalQueryError(
                        "lexical query matched more than "
                        f"{self._policy.max_candidates} chunks; refine the query"
                    )
                ranked_hits = [
                    _row_to_search_hit(
                        row,
                        normalized_query=normalized_query,
                        score_terms=score_terms,
                    )
                    for row in rows
                ]
                hits = tuple(
                    sorted(
                        ranked_hits,
                        key=lambda hit: (
                            -hit.score,
                            hit.path,
                            hit.chunk_index,
                        ),
                    )[:result_limit]
                )
                skipped, lossy = coverage_paths(connection, repository_id)

            return LexicalSearchResponse(
                query=normalized_query,
                snapshot_fingerprint=snapshot.fingerprint_sha256,
                hits=hits,
                skipped_paths=skipped,
                lossy_paths=lossy,
            )
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                raise LexicalIndexNotReady(
                    "lexical index schema is unavailable; run database migrations first"
                ) from exc
            raise LexicalIndexError("SQLite lexical query failed") from exc


def _lexical_candidates(
    snapshot: RepositorySnapshot,
) -> dict[str, RepositoryEntry]:
    return {
        entry.path: entry
        for entry in snapshot.entries
        if entry.kind is RepositoryEntryKind.FILE
        and entry.content_kind
        in {RepositoryContentKind.TEXT, RepositoryContentKind.EMPTY}
    }


def _index_entry(
    connection: sqlite3.Connection,
    repository_id: str,
    snapshot: RepositorySnapshot,
    entry: RepositoryEntry,
    policy: LexicalIndexPolicy,
) -> None:
    path_key = encode_path(entry.path)
    if entry.size_bytes > policy.max_file_bytes:
        connection.execute(
            """
            INSERT INTO lexical_files(
                repository_id, path, file_sha256, size_bytes, language,
                content_kind, status, decode_lossy, chunk_count
            ) VALUES (?, ?, ?, ?, ?, ?, 'skipped_size', 0, 0)
            """,
            (
                repository_id,
                path_key,
                entry.sha256,
                entry.size_bytes,
                entry.language,
                entry.content_kind.value,
            ),
        )
        return

    payload = read_snapshot_file(
        snapshot,
        entry.path,
        max_bytes=policy.max_file_bytes,
    )
    text, decode_lossy = decode_text(payload)
    chunks = chunk_text(text, entry, policy)

    connection.execute(
        """
        INSERT INTO lexical_files(
            repository_id, path, file_sha256, size_bytes, language,
            content_kind, status, decode_lossy, chunk_count
        ) VALUES (?, ?, ?, ?, ?, ?, 'indexed', ?, ?)
        """,
        (
            repository_id,
            path_key,
            entry.sha256,
            entry.size_bytes,
            entry.language,
            entry.content_kind.value,
            int(decode_lossy),
            len(chunks),
        ),
    )
    for chunk in chunks:
        connection.execute(
            """
            INSERT INTO lexical_chunks(
                repository_id, path, chunk_index, chunk_id,
                start_line, end_line, content, content_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repository_id,
                path_key,
                chunk.chunk_index,
                chunk.chunk_id,
                chunk.start_line,
                chunk.end_line,
                chunk.content,
                chunk.content_sha256,
            ),
        )


def _row_to_search_hit(
    row: sqlite3.Row,
    *,
    normalized_query: str,
    score_terms: tuple[str, ...],
) -> LexicalSearchHit:
    path = decode_path(str(row["path"]))
    content = str(row["content"])
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    durable_content_sha256 = str(row["content_sha256"])
    if content_sha256 != durable_content_sha256:
        raise LexicalIndexError(
            f"lexical chunk content hash is inconsistent: {path}"
        )
    expected_chunk_id = chunk_id(
        path=path,
        file_sha256=str(row["file_sha256"]),
        chunk_index=int(row["chunk_index"]),
        start_line=int(row["start_line"]),
        end_line=int(row["end_line"]),
        content_sha256=durable_content_sha256,
    )
    if expected_chunk_id != str(row["chunk_id"]):
        raise LexicalIndexError(
            f"lexical chunk identity is inconsistent: {path}"
        )
    return LexicalSearchHit(
        chunk_id=expected_chunk_id,
        path=path,
        chunk_index=int(row["chunk_index"]),
        start_line=int(row["start_line"]),
        end_line=int(row["end_line"]),
        content=content,
        content_sha256=durable_content_sha256,
        language=None if row["language"] is None else str(row["language"]),
        decode_lossy=bool(row["decode_lossy"]),
        score=_deterministic_score(
            path,
            content,
            normalized_query,
            score_terms,
        ),
    )


def _sync_result(
    snapshot: RepositorySnapshot,
    files: dict[str, FileState],
    *,
    rebuilt: tuple[str, ...],
    removed: tuple[str, ...],
    unchanged: tuple[str, ...] | None = None,
) -> LexicalSyncResult:
    skipped = tuple(
        sorted(
            path
            for path, state in files.items()
            if state.status is LexicalFileStatus.SKIPPED_SIZE
        )
    )
    lossy = tuple(
        sorted(path for path, state in files.items() if state.decode_lossy)
    )
    if unchanged is None:
        unchanged = tuple(sorted(set(files) - set(rebuilt)))
    return LexicalSyncResult(
        snapshot_fingerprint=snapshot.fingerprint_sha256,
        rebuilt_paths=tuple(sorted(rebuilt)),
        removed_paths=tuple(sorted(removed)),
        unchanged_paths=tuple(sorted(unchanged)),
        skipped_paths=skipped,
        lossy_paths=lossy,
        chunk_count=sum(state.chunk_count for state in files.values()),
    )


def _prepare_query(
    query: str,
    policy: LexicalIndexPolicy,
) -> tuple[str, str, tuple[str, ...]]:
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    normalized = query.strip()
    if not normalized:
        raise LexicalQueryError("query must not be empty")
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise LexicalQueryError("query must contain valid Unicode text") from exc
    if len(normalized) > policy.max_query_chars:
        raise LexicalQueryError(
            f"query exceeds the {policy.max_query_chars}-character limit"
        )

    tokens: list[str] = []
    seen: set[str] = set()
    for match in _QUERY_TOKEN.finditer(normalized):
        token = match.group(0)
        if not any(char.isalnum() for char in token):
            continue
        folded = token.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        tokens.append(token)
    if not tokens:
        raise LexicalQueryError("query contains no searchable lexical terms")
    if len(tokens) > policy.max_query_terms:
        raise LexicalQueryError(
            f"query contains more than {policy.max_query_terms} searchable terms"
        )
    fts_query = " AND ".join(f'"{token}"' for token in tokens)
    score_terms: list[str] = []
    score_seen: set[str] = set()
    for token in tokens:
        parts = [part for part in token.split("_") if part]
        for part in parts or [token]:
            folded = part.casefold()
            if folded not in score_seen:
                score_seen.add(folded)
                score_terms.append(folded)
    return normalized, fts_query, tuple(score_terms)



def _deterministic_score(
    path: str,
    content: str,
    normalized_query: str,
    score_terms: tuple[str, ...],
) -> int:
    normalized_path = _score_normalize(path)
    normalized_content = _score_normalize(content)
    path_terms = re.findall(r"[^\W_]+", normalized_path, flags=re.UNICODE)
    content_terms = re.findall(r"[^\W_]+", normalized_content, flags=re.UNICODE)

    score = 0
    for term in score_terms:
        normalized_term = _score_normalize(term)
        score += 100 * min(path_terms.count(normalized_term), 5)
        score += 10 * min(content_terms.count(normalized_term), 20)

    folded_query = _score_normalize(normalized_query)
    if folded_query in normalized_path:
        score += 250
    if folded_query in normalized_content:
        score += 25
    return score


def _score_normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in folded if not unicodedata.combining(char))

def _repository_id(repository_root: Path) -> str:
    return hashlib.sha256(os.fsencode(repository_root)).hexdigest()


def _policy_fingerprint(policy: LexicalIndexPolicy) -> str:
    payload = {
        "index_format_revision": _INDEX_FORMAT_REVISION,
        "policy": asdict(policy),
        "sqlite_version": sqlite3.sqlite_version,
        "unicode_version": unicodedata.unidata_version,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
