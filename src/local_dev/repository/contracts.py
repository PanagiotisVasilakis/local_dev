from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType


class RepositoryScanError(RuntimeError):
    """Base class for deterministic repository scanning failures."""


class RepositoryScanRaceError(RepositoryScanError):
    """The repository changed while a snapshot was being constructed."""


class RepositoryEntryKind(StrEnum):
    FILE = "file"
    SYMLINK = "symlink"
    SPECIAL = "special"


class RepositoryContentKind(StrEnum):
    EMPTY = "empty"
    TEXT = "text"
    BINARY = "binary"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class RepositoryEntry:
    path: str
    kind: RepositoryEntryKind
    size_bytes: int
    sha256: str
    executable: bool
    content_kind: RepositoryContentKind
    language: str | None = None

    def __post_init__(self) -> None:
        path = _normalized_repo_path(self.path)
        if not isinstance(self.kind, RepositoryEntryKind):
            raise TypeError("kind must be RepositoryEntryKind")
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes < 0
        ):
            raise ValueError("size_bytes must be a non-negative integer")
        digest = _normalized_sha256(self.sha256)
        if not isinstance(self.executable, bool):
            raise TypeError("executable must be bool")
        if not isinstance(self.content_kind, RepositoryContentKind):
            raise TypeError("content_kind must be RepositoryContentKind")
        language = self.language
        if language is not None:
            if not isinstance(language, str):
                raise TypeError("language must be a string when present")
            language = language.strip()
            if not language:
                raise ValueError("language must be non-empty when present")
        if self.kind is RepositoryEntryKind.FILE:
            if self.content_kind is RepositoryContentKind.NOT_APPLICABLE:
                raise ValueError("regular files require a content classification")
        elif self.content_kind is not RepositoryContentKind.NOT_APPLICABLE:
            raise ValueError("non-regular entries must use NOT_APPLICABLE content kind")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "sha256", digest)
        object.__setattr__(self, "language", language)


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    repository_root: Path
    entries: tuple[RepositoryEntry, ...]
    fingerprint_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.repository_root, Path):
            raise TypeError("repository_root must be pathlib.Path")
        if not self.repository_root.is_absolute():
            raise ValueError("repository_root must be absolute")
        try:
            entries = tuple(self.entries)
        except TypeError as exc:
            raise TypeError("entries must be iterable RepositoryEntry values") from exc
        if not all(isinstance(entry, RepositoryEntry) for entry in entries):
            raise TypeError("entries must contain RepositoryEntry values")
        paths = [entry.path for entry in entries]
        if paths != sorted(paths):
            raise ValueError("repository entries must be sorted by path")
        if len(paths) != len(set(paths)):
            raise ValueError("repository entries must have unique paths")
        fingerprint = _normalized_sha256(self.fingerprint_sha256)
        expected = repository_fingerprint(entries)
        if fingerprint != expected:
            raise ValueError("repository fingerprint does not match entries")
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "fingerprint_sha256", fingerprint)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size_bytes for entry in self.entries)

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @property
    def regular_file_count(self) -> int:
        return sum(entry.kind is RepositoryEntryKind.FILE for entry in self.entries)

    @property
    def language_counts(self) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            if entry.language is not None:
                counts[entry.language] = counts.get(entry.language, 0) + 1
        return MappingProxyType(dict(sorted(counts.items())))


@dataclass(frozen=True, slots=True)
class RepositoryDiff:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    modified: tuple[str, ...]
    metadata_only: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("added", "removed", "modified", "metadata_only"):
            values = getattr(self, name)
            try:
                normalized = tuple(_normalized_repo_path(value) for value in values)
            except TypeError as exc:
                raise TypeError(f"{name} must be an iterable of repository paths") from exc
            if normalized != tuple(sorted(set(normalized))):
                raise ValueError(f"{name} must contain unique sorted repository paths")
            object.__setattr__(self, name, normalized)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.modified or self.metadata_only)


def repository_fingerprint(entries: tuple[RepositoryEntry, ...]) -> str:
    try:
        normalized_entries = tuple(entries)
    except TypeError as exc:
        raise TypeError("entries must be iterable RepositoryEntry values") from exc
    if not all(isinstance(entry, RepositoryEntry) for entry in normalized_entries):
        raise TypeError("entries must contain RepositoryEntry values")
    paths = [entry.path for entry in normalized_entries]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("entries must be uniquely sorted by path")
    payload = [
        {
            "path": entry.path,
            "kind": entry.kind.value,
            "size_bytes": entry.size_bytes,
            "sha256": entry.sha256,
            "executable": entry.executable,
            "content_kind": entry.content_kind.value,
            "language": entry.language,
        }
        for entry in normalized_entries
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compare_snapshots(before: RepositorySnapshot, after: RepositorySnapshot) -> RepositoryDiff:
    if not isinstance(before, RepositorySnapshot) or not isinstance(after, RepositorySnapshot):
        raise TypeError("before and after must be RepositorySnapshot")
    before_by_path = {entry.path: entry for entry in before.entries}
    after_by_path = {entry.path: entry for entry in after.entries}

    before_paths = set(before_by_path)
    after_paths = set(after_by_path)
    added = tuple(sorted(after_paths - before_paths))
    removed = tuple(sorted(before_paths - after_paths))

    modified: list[str] = []
    metadata_only: list[str] = []
    for path in sorted(before_paths & after_paths):
        old = before_by_path[path]
        new = after_by_path[path]
        if old.sha256 != new.sha256 or old.size_bytes != new.size_bytes:
            modified.append(path)
        elif old != new:
            metadata_only.append(path)

    return RepositoryDiff(
        added=added,
        removed=removed,
        modified=tuple(modified),
        metadata_only=tuple(metadata_only),
    )


def render_repo_map(snapshot: RepositorySnapshot) -> str:
    if not isinstance(snapshot, RepositorySnapshot):
        raise TypeError("snapshot must be RepositorySnapshot")
    lines = [
        "repository: " + json.dumps(snapshot.repository_root.name, ensure_ascii=True),
        f"fingerprint: {snapshot.fingerprint_sha256}",
        f"entries: {snapshot.entry_count}",
        f"bytes: {snapshot.total_bytes}",
    ]
    if snapshot.language_counts:
        languages = ", ".join(
            f"{language}={count}" for language, count in snapshot.language_counts.items()
        )
        lines.append(f"languages: {languages}")
    else:
        lines.append("languages: none")
    lines.append("paths:")
    for entry in snapshot.entries:
        language = entry.language or "-"
        lines.append(
            "\t".join(
                (
                    json.dumps(entry.path, ensure_ascii=True),
                    entry.kind.value,
                    entry.content_kind.value,
                    language,
                    str(entry.size_bytes),
                    "x" if entry.executable else "-",
                    entry.sha256[:12],
                )
            )
        )
    return "\n".join(lines) + "\n"


def _normalized_repo_path(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("repository path must be a string")
    if not value:
        raise ValueError("repository path must be non-empty")
    if "\x00" in value:
        raise ValueError("repository path must not contain NUL")
    pure = PurePosixPath(value)
    invalid_part = any(part in {"", ".", ".."} for part in pure.parts)
    if pure.is_absolute() or value in {".", ".."} or invalid_part:
        raise ValueError("repository path must stay relative to the repository root")
    normalized = pure.as_posix()
    if normalized != value:
        raise ValueError("repository path must be normalized")
    return normalized


def _normalized_sha256(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("sha256 must be a string")
    digest = value.strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("sha256 must be a 64-character hexadecimal digest")
    return digest
