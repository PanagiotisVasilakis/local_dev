from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LexicalIndexError(RuntimeError):
    """Base class for deterministic lexical-index failures."""


class LexicalIndexNotReady(LexicalIndexError):
    """No lexical index exists for the supplied repository snapshot."""


class LexicalIndexStale(LexicalIndexError):
    """The durable lexical index does not match the supplied snapshot or policy."""


class LexicalQueryError(LexicalIndexError):
    """A lexical query cannot be represented safely by the supported query contract."""


class LexicalFileStatus(StrEnum):
    INDEXED = "indexed"
    SKIPPED_SIZE = "skipped_size"


@dataclass(frozen=True, slots=True)
class LexicalIndexPolicy:
    max_file_bytes: int = 8 * 1024 * 1024
    max_chunk_chars: int = 6000
    chunk_lines: int = 120
    overlap_lines: int = 12
    max_chunks_per_file: int = 5000
    max_query_chars: int = 1000
    max_query_terms: int = 32
    max_candidates: int = 2000
    default_limit: int = 12
    max_results: int = 100

    def __post_init__(self) -> None:
        for name in (
            "max_file_bytes",
            "max_chunk_chars",
            "chunk_lines",
            "max_chunks_per_file",
            "max_query_chars",
            "max_query_terms",
            "max_candidates",
            "default_limit",
            "max_results",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (
            not isinstance(self.overlap_lines, int)
            or isinstance(self.overlap_lines, bool)
            or self.overlap_lines < 0
        ):
            raise ValueError("overlap_lines must be a non-negative integer")
        if self.overlap_lines >= self.chunk_lines:
            raise ValueError("overlap_lines must be smaller than chunk_lines")
        if self.default_limit > self.max_results:
            raise ValueError("default_limit must not exceed max_results")


@dataclass(frozen=True, slots=True)
class LexicalSearchHit:
    chunk_id: str
    path: str
    chunk_index: int
    start_line: int
    end_line: int
    content: str
    content_sha256: str
    language: str | None
    decode_lossy: bool
    score: int

    def __post_init__(self) -> None:
        for name in ("chunk_id", "content_sha256"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("path must be non-empty")
        if (
            not isinstance(self.chunk_index, int)
            or isinstance(self.chunk_index, bool)
            or self.chunk_index < 0
        ):
            raise ValueError("chunk_index must be a non-negative integer")
        for name in ("start_line", "end_line"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line")
        if not isinstance(self.content, str):
            raise TypeError("content must be a string")
        if self.language is not None and (
            not isinstance(self.language, str) or not self.language.strip()
        ):
            raise ValueError("language must be non-empty when present")
        if not isinstance(self.decode_lossy, bool):
            raise TypeError("decode_lossy must be bool")
        if (
            not isinstance(self.score, int)
            or isinstance(self.score, bool)
            or self.score < 0
        ):
            raise ValueError("score must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class LexicalSearchResponse:
    query: str
    snapshot_fingerprint: str
    hits: tuple[LexicalSearchHit, ...]
    skipped_paths: tuple[str, ...]
    lossy_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("query must be non-empty")
        _require_digest(self.snapshot_fingerprint, "snapshot_fingerprint")
        hits = tuple(self.hits)
        if not all(isinstance(hit, LexicalSearchHit) for hit in hits):
            raise TypeError("hits must contain LexicalSearchHit values")
        skipped = _sorted_paths(self.skipped_paths, "skipped_paths")
        lossy = _sorted_paths(self.lossy_paths, "lossy_paths")
        object.__setattr__(self, "hits", hits)
        object.__setattr__(self, "skipped_paths", skipped)
        object.__setattr__(self, "lossy_paths", lossy)

    @property
    def coverage_complete(self) -> bool:
        return not self.skipped_paths and not self.lossy_paths


@dataclass(frozen=True, slots=True)
class LexicalSyncResult:
    snapshot_fingerprint: str
    rebuilt_paths: tuple[str, ...]
    removed_paths: tuple[str, ...]
    unchanged_paths: tuple[str, ...]
    skipped_paths: tuple[str, ...]
    lossy_paths: tuple[str, ...]
    chunk_count: int

    def __post_init__(self) -> None:
        _require_digest(self.snapshot_fingerprint, "snapshot_fingerprint")
        for name in (
            "rebuilt_paths",
            "removed_paths",
            "unchanged_paths",
            "skipped_paths",
            "lossy_paths",
        ):
            object.__setattr__(self, name, _sorted_paths(getattr(self, name), name))
        if (
            not isinstance(self.chunk_count, int)
            or isinstance(self.chunk_count, bool)
            or self.chunk_count < 0
        ):
            raise ValueError("chunk_count must be a non-negative integer")


def _require_digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _sorted_paths(values: object, name: str) -> tuple[str, ...]:
    try:
        normalized = tuple(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{name} must be iterable") from exc
    if not all(isinstance(value, str) and value for value in normalized):
        raise ValueError(f"{name} must contain non-empty strings")
    expected = tuple(sorted(set(normalized)))
    if normalized != expected:
        raise ValueError(f"{name} must contain unique sorted paths")
    return normalized
