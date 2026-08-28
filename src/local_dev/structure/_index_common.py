from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from local_dev.repository.contracts import RepositoryEntry, RepositoryEntryKind, RepositorySnapshot
from local_dev.structure._storage_rows import (
    FileState,
    load_files,
    validate_file_set,
    validate_row_counts,
)
from local_dev.structure._storage_state import IndexState, load_state, validate_state_counts
from local_dev.structure.contracts import (
    StructuralIndexError,
    StructuralIndexNotReady,
    StructuralIndexPolicy,
    StructuralIndexStale,
    StructuralSyncResult,
)

if TYPE_CHECKING:
    from local_dev.structure.index import StructuralIndex

_FORMAT_REVISION = 2


def candidates(snapshot: RepositorySnapshot) -> dict[str, RepositoryEntry]:
    return {
        entry.path: entry
        for entry in snapshot.entries
        if entry.kind is RepositoryEntryKind.FILE
    }


def repository_id(root: Path) -> str:
    return hashlib.sha256(os.fsencode(root)).hexdigest()


def policy_fingerprint(policy: StructuralIndexPolicy) -> str:
    payload = {
        "format_revision": _FORMAT_REVISION,
        "policy": asdict(policy),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "parser_feature_version": "3.12",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def state_matches(state: IndexState, snapshot: RepositorySnapshot, fingerprint: str) -> bool:
    return (
        state.snapshot_fingerprint == snapshot.fingerprint_sha256
        and state.policy_fingerprint == fingerprint
    )


def validate_index(
    index: StructuralIndex,
    connection: sqlite3.Connection,
    repo_id: str,
    snapshot: RepositorySnapshot,
    desired: dict[str, RepositoryEntry],
    state: IndexState,
    files: dict[str, FileState],
) -> None:
    if not state_matches(state, snapshot, index._policy_fingerprint):
        raise StructuralIndexStale("structural index does not match supplied snapshot")
    validate_file_set(files, desired, index._policy)
    validate_row_counts(connection, repo_id, files)
    validate_state_counts(connection, repo_id, state)


@contextmanager
def validated_read(
    index: StructuralIndex,
    snapshot: RepositorySnapshot,
) -> Iterator[tuple[sqlite3.Connection, str, dict[str, FileState]]]:
    if not isinstance(snapshot, RepositorySnapshot):
        raise TypeError("snapshot must be RepositorySnapshot")
    repo_id = repository_id(snapshot.repository_root)
    try:
        with index._database.connect() as connection:
            state = load_state(connection, repo_id)
            if state is None:
                raise StructuralIndexNotReady("no structural index exists for repository")
            desired = candidates(snapshot)
            files = load_files(connection, repo_id)
            validate_index(index, connection, repo_id, snapshot, desired, state, files)
            yield connection, repo_id, files
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            raise StructuralIndexNotReady("structural schema is incomplete") from exc
        raise StructuralIndexError("SQLite structural read failed") from exc
    except sqlite3.DatabaseError as exc:
        raise StructuralIndexError("SQLite structural read failed") from exc


def sync_result(
    snapshot: RepositorySnapshot,
    files: dict[str, FileState],
    rebuilt: tuple[str, ...],
    removed: tuple[str, ...],
    unchanged: tuple[str, ...],
) -> StructuralSyncResult:
    reports = tuple(files[path].report() for path in sorted(files))
    return StructuralSyncResult(
        snapshot_fingerprint=snapshot.fingerprint_sha256,
        rebuilt_paths=tuple(sorted(rebuilt)),
        removed_paths=tuple(sorted(removed)),
        unchanged_paths=tuple(sorted(unchanged)),
        reports=reports,
        symbol_count=sum(item.symbol_count for item in reports),
        import_count=sum(item.import_count for item in reports),
    )


def nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value
