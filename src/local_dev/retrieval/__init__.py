"""Deterministic lexical retrieval over repository snapshots."""

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
from local_dev.retrieval.index import LexicalIndex

__all__ = [
    "LexicalFileStatus",
    "LexicalIndex",
    "LexicalIndexError",
    "LexicalIndexNotReady",
    "LexicalIndexPolicy",
    "LexicalIndexStale",
    "LexicalQueryError",
    "LexicalSearchHit",
    "LexicalSearchResponse",
    "LexicalSyncResult",
]
