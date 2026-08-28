from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from local_dev.repository.contracts import RepositorySnapshot
from local_dev.structure._index_common import (
    candidates,
    repository_id,
    state_matches,
    sync_result,
    validate_index,
)
from local_dev.structure._storage_rows import (
    delete_file,
    file_matches_entry,
    load_files,
    validate_file_set,
    validate_row_counts,
)
from local_dev.structure._storage_state import compute_digest, counts, load_state
from local_dev.structure._writer import index_entry
from local_dev.structure.contracts import (
    StructuralIndexError,
    StructuralIndexNotReady,
    StructuralSyncResult,
)

if TYPE_CHECKING:
    from local_dev.structure.index import StructuralIndex


def sync(index: StructuralIndex, snapshot: RepositorySnapshot) -> StructuralSyncResult:
    if not isinstance(snapshot, RepositorySnapshot):
        raise TypeError("snapshot must be RepositorySnapshot")
    repo_id = repository_id(snapshot.repository_root)
    desired = candidates(snapshot)
    try:
        with index._database.immediate_transaction() as connection:
            state = load_state(connection, repo_id)
            existing = load_files(connection, repo_id)
            if state is not None and state_matches(
                state,
                snapshot,
                index._policy_fingerprint,
            ):
                validate_index(index, connection, repo_id, snapshot, desired, state, existing)
                return sync_result(snapshot, existing, (), (), tuple(sorted(desired)))

            now = datetime.now(UTC).isoformat()
            if state is None:
                connection.execute(
                    """
                    INSERT INTO structural_index_state(
                        repository_id, snapshot_fingerprint, policy_fingerprint,
                        structure_digest, file_count, symbol_count, import_count, updated_at
                    ) VALUES (?, ?, ?, ?, 0, 0, 0, ?)
                    """,
                    (
                        repo_id,
                        snapshot.fingerprint_sha256,
                        index._policy_fingerprint,
                        "0" * 64,
                        now,
                    ),
                )

            rebuild_all = state is None or state.policy_fingerprint != index._policy_fingerprint
            if rebuild_all:
                removed = tuple(sorted(set(existing) - set(desired)))
                rebuild = tuple(sorted(desired))
                _delete_all(connection, repo_id)
                existing = {}
            else:
                removed = tuple(sorted(set(existing) - set(desired)))
                rebuild = tuple(
                    sorted(
                        path
                        for path, entry in desired.items()
                        if not file_matches_entry(existing.get(path), entry, index._policy)
                    )
                )
                for path in (*removed, *rebuild):
                    if path in existing:
                        delete_file(connection, repo_id, path)

            for path in rebuild:
                index_entry(connection, repo_id, snapshot, desired[path], index._policy)

            files = load_files(connection, repo_id)
            validate_file_set(files, desired, index._policy)
            validate_row_counts(connection, repo_id, files)
            file_count, symbol_count, import_count = counts(connection, repo_id)
            digest = compute_digest(connection, repo_id)
            connection.execute(
                """
                UPDATE structural_index_state
                SET snapshot_fingerprint=?, policy_fingerprint=?, structure_digest=?,
                    file_count=?, symbol_count=?, import_count=?, updated_at=?
                WHERE repository_id=?
                """,
                (
                    snapshot.fingerprint_sha256,
                    index._policy_fingerprint,
                    digest,
                    file_count,
                    symbol_count,
                    import_count,
                    now,
                    repo_id,
                ),
            )
            final_state = load_state(connection, repo_id)
            if final_state is None:
                raise StructuralIndexError("structural state disappeared during sync")
            validate_index(index, connection, repo_id, snapshot, desired, final_state, files)
            unchanged = tuple(sorted(set(desired) - set(rebuild)))
            return sync_result(snapshot, files, rebuild, removed, unchanged)
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            raise StructuralIndexNotReady(
                "structural schema unavailable; run migrations"
            ) from exc
        raise StructuralIndexError("SQLite structural synchronization failed") from exc
    except sqlite3.DatabaseError as exc:
        raise StructuralIndexError("SQLite structural synchronization failed") from exc


def _delete_all(connection: sqlite3.Connection, repo_id: str) -> None:
    connection.execute("DELETE FROM structural_imports WHERE repository_id=?", (repo_id,))
    connection.execute("DELETE FROM structural_symbols WHERE repository_id=?", (repo_id,))
    connection.execute("DELETE FROM structural_files WHERE repository_id=?", (repo_id,))
