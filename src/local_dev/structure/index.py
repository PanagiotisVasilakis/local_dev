from __future__ import annotations

from local_dev.db import Database
from local_dev.repository.contracts import RepositorySnapshot
from local_dev.structure import _query, _sync
from local_dev.structure._index_common import policy_fingerprint
from local_dev.structure.contracts import (
    ImportKind,
    StructuralFileReport,
    StructuralImport,
    StructuralIndexPolicy,
    StructuralSymbol,
    StructuralSyncResult,
    SymbolKind,
)


class StructuralIndex:
    """Deterministic structural definitions/imports bound to repository snapshots."""

    def __init__(self, database: Database, policy: StructuralIndexPolicy | None = None) -> None:
        if not isinstance(database, Database):
            raise TypeError("database must be Database")
        if policy is not None and not isinstance(policy, StructuralIndexPolicy):
            raise TypeError("policy must be StructuralIndexPolicy")
        self._database = database
        self._policy = policy or StructuralIndexPolicy()
        self._policy_fingerprint = policy_fingerprint(self._policy)

    def sync(self, snapshot: RepositorySnapshot) -> StructuralSyncResult:
        return _sync.sync(self, snapshot)

    def files(self, snapshot: RepositorySnapshot) -> tuple[StructuralFileReport, ...]:
        return _query.files(self, snapshot)

    def symbols(
        self,
        snapshot: RepositorySnapshot,
        *,
        name: str | None = None,
        qualified_name: str | None = None,
        path: str | None = None,
        kind: SymbolKind | None = None,
        parent_qualified_name: str | None = None,
        limit: int | None = None,
    ) -> tuple[StructuralSymbol, ...]:
        return _query.symbols(
            self,
            snapshot,
            name=name,
            qualified_name=qualified_name,
            path=path,
            kind=kind,
            parent_qualified_name=parent_qualified_name,
            limit=limit,
        )

    def imports(
        self,
        snapshot: RepositorySnapshot,
        *,
        module: str | None = None,
        name: str | None = None,
        path: str | None = None,
        kind: ImportKind | None = None,
        scope_qualified_name: str | None = None,
        limit: int | None = None,
    ) -> tuple[StructuralImport, ...]:
        return _query.imports(
            self,
            snapshot,
            module=module,
            name=name,
            path=path,
            kind=kind,
            scope_qualified_name=scope_qualified_name,
            limit=limit,
        )

    def _limit(self, limit: int | None) -> int:
        result = self._policy.default_limit if limit is None else limit
        if (
            not isinstance(result, int)
            or isinstance(result, bool)
            or result <= 0
            or result > self._policy.max_results
        ):
            raise ValueError(f"limit must be between 1 and {self._policy.max_results}")
        return result
