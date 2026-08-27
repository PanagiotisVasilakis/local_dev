from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from local_dev.repository.contracts import (
    RepositoryContentKind,
    RepositoryEntry,
    RepositoryEntryKind,
    RepositoryScanError,
    RepositoryScanRaceError,
    RepositorySnapshot,
    repository_fingerprint,
)
from local_dev.repository.ignore import RepositoryIgnoreRules

_DEFAULT_EXCLUDED_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    }
)

_LANGUAGE_BY_SUFFIX: Mapping[str, str] = MappingProxyType(
    {
        ".c": "C",
        ".cc": "C++",
        ".cpp": "C++",
        ".cs": "C#",
        ".css": "CSS",
        ".dart": "Dart",
        ".ex": "Elixir",
        ".exs": "Elixir",
        ".go": "Go",
        ".graphql": "GraphQL",
        ".gql": "GraphQL",
        ".h": "C",
        ".hpp": "C++",
        ".html": "HTML",
        ".htm": "HTML",
        ".java": "Java",
        ".js": "JavaScript",
        ".jsx": "JavaScript",
        ".json": "JSON",
        ".kt": "Kotlin",
        ".kts": "Kotlin",
        ".lua": "Lua",
        ".md": "Markdown",
        ".mdx": "Markdown",
        ".php": "PHP",
        ".proto": "Protocol Buffers",
        ".py": "Python",
        ".pyi": "Python",
        ".rb": "Ruby",
        ".rs": "Rust",
        ".scala": "Scala",
        ".scss": "SCSS",
        ".sh": "Shell",
        ".sql": "SQL",
        ".svelte": "Svelte",
        ".swift": "Swift",
        ".tf": "Terraform",
        ".tfvars": "Terraform",
        ".toml": "TOML",
        ".ts": "TypeScript",
        ".tsx": "TypeScript",
        ".vue": "Vue",
        ".xml": "XML",
        ".yaml": "YAML",
        ".yml": "YAML",
    }
)

_LANGUAGE_BY_BASENAME: Mapping[str, str] = MappingProxyType(
    {
        "gemfile": "Ruby",
        "justfile": "Just",
        "makefile": "Makefile",
        "procfile": "Procfile",
        "rakefile": "Ruby",
    }
)


@dataclass(frozen=True, slots=True)
class RepositoryScanPolicy:
    excluded_names: frozenset[str] = field(default_factory=lambda: _DEFAULT_EXCLUDED_NAMES)
    max_entries: int = 200_000
    hash_chunk_bytes: int = 1024 * 1024
    text_sample_bytes: int = 8192
    max_ignore_file_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        if isinstance(self.excluded_names, (str, bytes)):
            raise TypeError("excluded_names must be an iterable of path names")
        try:
            names = frozenset(self.excluded_names)
        except TypeError as exc:
            raise TypeError("excluded_names must be an iterable of path names") from exc
        for name in names:
            if (
                not isinstance(name, str)
                or not name
                or "\x00" in name
                or "/" in name
                or name in {".", ".."}
            ):
                raise ValueError("excluded_names must contain valid single path names")
        for field_name in (
            "max_entries",
            "hash_chunk_bytes",
            "text_sample_bytes",
            "max_ignore_file_bytes",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        object.__setattr__(self, "excluded_names", names)


@dataclass(frozen=True, slots=True)
class _StatSignature:
    mode: int
    device: int
    inode: int
    size: int
    rdev: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True, slots=True)
class _ManifestItem:
    path: str
    kind: RepositoryEntryKind
    signature: _StatSignature


class RepositoryScanner:
    """Build deterministic repository snapshots without following symlink targets."""

    def __init__(self, policy: RepositoryScanPolicy | None = None) -> None:
        if policy is not None and not isinstance(policy, RepositoryScanPolicy):
            raise TypeError("policy must be RepositoryScanPolicy")
        self._policy = policy or RepositoryScanPolicy()

    def scan(self, repository_root: Path) -> RepositorySnapshot:
        _require_secure_dir_fd_support()
        root, root_signature = _validated_root(repository_root)
        root_fd = _open_directory_path(root)
        try:
            opened_signature = _stat_signature(os.fstat(root_fd))
            if opened_signature != root_signature:
                raise RepositoryScanRaceError("repository_root changed before scanning")

            first_raw = self._collect_raw_manifest(root_fd)
            first_selected = self._apply_ignore_rules(root_fd, first_raw)
            entries = tuple(
                self._entry_from_manifest(root_fd, item) for item in first_selected
            )

            second_raw = self._collect_raw_manifest(root_fd)
            second_selected = self._apply_ignore_rules(root_fd, second_raw)
            if _manifest_identity(first_selected) != _manifest_identity(second_selected):
                raise RepositoryScanRaceError(
                    "repository changed while the deterministic snapshot was being constructed"
                )
            if _stat_signature(os.fstat(root_fd)) != opened_signature:
                raise RepositoryScanRaceError("repository_root changed while scanning")

            fingerprint = repository_fingerprint(entries)
            return RepositorySnapshot(
                repository_root=root,
                entries=entries,
                fingerprint_sha256=fingerprint,
            )
        finally:
            os.close(root_fd)

    def _collect_raw_manifest(self, root_fd: int) -> tuple[_ManifestItem, ...]:
        items: list[_ManifestItem] = []
        self._walk_directory(root_fd, "", items)
        if len(items) > self._policy.max_entries:
            raise RepositoryScanError(
                f"repository contains more than {self._policy.max_entries} scannable entries"
            )
        return tuple(sorted(items, key=lambda item: item.path))

    def _walk_directory(
        self,
        directory_fd: int,
        relative_directory: str,
        items: list[_ManifestItem],
    ) -> None:
        try:
            with os.scandir(directory_fd) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            label = relative_directory or "."
            raise RepositoryScanError(
                f"failed to enumerate repository directory: {label}"
            ) from exc

        for entry in entries:
            if entry.name in self._policy.excluded_names:
                continue
            relative = (
                entry.name
                if not relative_directory
                else f"{relative_directory}/{entry.name}"
            )
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise RepositoryScanError(
                    f"failed to stat repository entry: {relative}"
                ) from exc

            mode = info.st_mode
            if stat.S_ISDIR(mode):
                child_fd = _open_child_directory(
                    directory_fd,
                    entry.name,
                    expected=_stat_signature(info),
                    path=relative,
                )
                try:
                    self._walk_directory(child_fd, relative, items)
                finally:
                    os.close(child_fd)
                continue
            if stat.S_ISREG(mode):
                kind = RepositoryEntryKind.FILE
            elif stat.S_ISLNK(mode):
                kind = RepositoryEntryKind.SYMLINK
            else:
                kind = RepositoryEntryKind.SPECIAL
            items.append(
                _ManifestItem(
                    path=relative,
                    kind=kind,
                    signature=_stat_signature(info),
                )
            )
            if len(items) > self._policy.max_entries:
                raise RepositoryScanError(
                    f"repository contains more than {self._policy.max_entries} scannable entries"
                )

    def _apply_ignore_rules(
        self,
        root_fd: int,
        raw: tuple[_ManifestItem, ...],
    ) -> tuple[_ManifestItem, ...]:
        ignore_sources: list[tuple[str, str]] = []
        for item in raw:
            if (
                item.kind is RepositoryEntryKind.FILE
                and PurePosixPath(item.path).name == ".gitignore"
            ):
                content = self._read_regular_file_bytes(
                    root_fd,
                    item,
                    max_bytes=self._policy.max_ignore_file_bytes,
                    purpose="ignore file",
                )
                text = content.decode("utf-8", errors="surrogateescape")
                ignore_sources.append((item.path, text))

        try:
            matcher = RepositoryIgnoreRules.from_sources(tuple(ignore_sources))
        except ValueError as exc:
            raise RepositoryScanError("repository contains an invalid .gitignore pattern") from exc
        selected = [
            item
            for item in raw
            if (
                PurePosixPath(item.path).name == ".gitignore"
                and not matcher.has_ignored_ancestor(item.path)
            )
            or not matcher.is_ignored(item.path, is_dir=False)
        ]
        return tuple(sorted(selected, key=lambda item: item.path))

    def _entry_from_manifest(self, root_fd: int, item: _ManifestItem) -> RepositoryEntry:
        if item.kind is RepositoryEntryKind.FILE:
            digest, sample = self._hash_regular_file(root_fd, item)
            content_kind = _classify_content(item.signature.size, sample)
            return RepositoryEntry(
                path=item.path,
                kind=item.kind,
                size_bytes=item.signature.size,
                sha256=digest,
                executable=_is_executable(item.signature.mode),
                content_kind=content_kind,
                language=detect_language(item.path),
            )
        if item.kind is RepositoryEntryKind.SYMLINK:
            target_bytes = self._read_symlink_bytes(root_fd, item)
            return RepositoryEntry(
                path=item.path,
                kind=item.kind,
                size_bytes=len(target_bytes),
                sha256=hashlib.sha256(target_bytes).hexdigest(),
                executable=False,
                content_kind=RepositoryContentKind.NOT_APPLICABLE,
                language=None,
            )

        special_payload = (
            f"special:{stat.S_IFMT(item.signature.mode)}:{item.signature.rdev}"
        ).encode("ascii")
        return RepositoryEntry(
            path=item.path,
            kind=item.kind,
            size_bytes=item.signature.size,
            sha256=hashlib.sha256(special_payload).hexdigest(),
            executable=_is_executable(item.signature.mode),
            content_kind=RepositoryContentKind.NOT_APPLICABLE,
            language=None,
        )

    def _hash_regular_file(
        self,
        root_fd: int,
        item: _ManifestItem,
    ) -> tuple[str, bytes]:
        descriptor = _open_relative_regular_file(root_fd, item)
        try:
            before = _stat_signature(os.fstat(descriptor))
            if before != item.signature:
                raise RepositoryScanRaceError(
                    f"repository file changed before hashing: {item.path}"
                )
            hasher = hashlib.sha256()
            sample = bytearray()
            while True:
                chunk = os.read(descriptor, self._policy.hash_chunk_bytes)
                if not chunk:
                    break
                hasher.update(chunk)
                if len(sample) < self._policy.text_sample_bytes:
                    remaining = self._policy.text_sample_bytes - len(sample)
                    sample.extend(chunk[:remaining])
            after = _stat_signature(os.fstat(descriptor))
            if after != before:
                raise RepositoryScanRaceError(
                    f"repository file changed while hashing: {item.path}"
                )
            return hasher.hexdigest(), bytes(sample)
        except OSError as exc:
            raise RepositoryScanError(f"failed to read repository file: {item.path}") from exc
        finally:
            os.close(descriptor)

    def _read_regular_file_bytes(
        self,
        root_fd: int,
        item: _ManifestItem,
        *,
        max_bytes: int,
        purpose: str,
    ) -> bytes:
        if item.signature.size > max_bytes:
            raise RepositoryScanError(
                f"{purpose} exceeds the {max_bytes}-byte safety limit: {item.path}"
            )
        descriptor = _open_relative_regular_file(root_fd, item)
        try:
            before = _stat_signature(os.fstat(descriptor))
            if before != item.signature:
                raise RepositoryScanRaceError(
                    f"repository {purpose} changed before reading: {item.path}"
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(
                    descriptor,
                    min(self._policy.hash_chunk_bytes, max_bytes + 1),
                )
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise RepositoryScanError(
                        f"{purpose} exceeds the {max_bytes}-byte safety limit: {item.path}"
                    )
                chunks.append(chunk)
            after = _stat_signature(os.fstat(descriptor))
            if after != before:
                raise RepositoryScanRaceError(
                    f"repository {purpose} changed while reading: {item.path}"
                )
            return b"".join(chunks)
        except OSError as exc:
            raise RepositoryScanError(
                f"failed to read repository {purpose}: {item.path}"
            ) from exc
        finally:
            os.close(descriptor)

    def _read_symlink_bytes(self, root_fd: int, item: _ManifestItem) -> bytes:
        parent_fd, name = _open_relative_parent(root_fd, item.path)
        try:
            before = _stat_signature(
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            )
            if before != item.signature or not stat.S_ISLNK(before.mode):
                raise RepositoryScanRaceError(
                    f"repository symlink changed before reading: {item.path}"
                )
            target = os.readlink(name, dir_fd=parent_fd)
            after = _stat_signature(
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            )
            if after != before:
                raise RepositoryScanRaceError(
                    f"repository symlink changed while reading: {item.path}"
                )
        except OSError as exc:
            raise RepositoryScanRaceError(
                f"repository symlink became unavailable: {item.path}"
            ) from exc
        finally:
            os.close(parent_fd)
        return os.fsencode(target)


def detect_language(path: str) -> str | None:
    if not isinstance(path, str):
        raise TypeError("path must be a string")
    if not path:
        raise ValueError("path must be non-empty")
    pure = PurePosixPath(path)
    lower_name = pure.name.lower()
    if lower_name.startswith("dockerfile"):
        return "Dockerfile"
    if lower_name in _LANGUAGE_BY_BASENAME:
        return _LANGUAGE_BY_BASENAME[lower_name]
    for suffix in reversed([suffix.lower() for suffix in pure.suffixes]):
        language = _LANGUAGE_BY_SUFFIX.get(suffix)
        if language is not None:
            return language
    return None


def _validated_root(value: Path) -> tuple[Path, _StatSignature]:
    if not isinstance(value, Path):
        raise TypeError("repository_root must be pathlib.Path")
    if not value.is_absolute():
        raise ValueError("repository_root must be absolute")
    try:
        initial = os.lstat(value)
    except OSError as exc:
        raise RepositoryScanError(
            "repository_root does not exist or cannot be stat'ed"
        ) from exc
    if stat.S_ISLNK(initial.st_mode):
        raise RepositoryScanError("repository_root must not be a symlink")
    if not stat.S_ISDIR(initial.st_mode):
        raise RepositoryScanError("repository_root must be a directory")
    try:
        resolved = value.resolve(strict=True)
        resolved_info = os.lstat(resolved)
    except OSError as exc:
        raise RepositoryScanError("repository_root could not be resolved") from exc
    if _identity_signature(initial) != _identity_signature(resolved_info):
        raise RepositoryScanRaceError("repository_root changed while being resolved")
    return resolved, _stat_signature(resolved_info)


def _require_secure_dir_fd_support() -> None:
    supported = (
        hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.readlink in os.supports_dir_fd
        and os.scandir in os.supports_fd
    )
    if not supported:
        raise RepositoryScanError(
            "this platform lacks the dir-fd/no-follow primitives required for secure scanning"
        )


def _open_directory_path(path: Path) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RepositoryScanRaceError(
            "repository_root became unavailable or changed type"
        ) from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RepositoryScanRaceError("repository_root is no longer a directory")
    return descriptor


def _open_child_directory(
    parent_fd: int,
    name: str,
    *,
    expected: _StatSignature,
    path: str,
) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
    )
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise RepositoryScanRaceError(
            f"repository directory became unavailable or changed type: {path}"
        ) from exc
    actual = _stat_signature(os.fstat(descriptor))
    if actual != expected:
        os.close(descriptor)
        raise RepositoryScanRaceError(
            f"repository directory changed before traversal: {path}"
        )
    return descriptor


def _open_relative_regular_file(root_fd: int, item: _ManifestItem) -> int:
    parent_fd, name = _open_relative_parent(root_fd, item.path)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_NOFOLLOW
    )
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise RepositoryScanRaceError(
            f"repository file became unavailable or changed type: {item.path}"
        ) from exc
    finally:
        os.close(parent_fd)
    actual = _stat_signature(os.fstat(descriptor))
    if actual != item.signature or not stat.S_ISREG(actual.mode):
        os.close(descriptor)
        raise RepositoryScanRaceError(
            f"repository file changed before opening: {item.path}"
        )
    return descriptor


def _open_relative_parent(root_fd: int, relative_path: str) -> tuple[int, str]:
    parts = PurePosixPath(relative_path).parts
    if not parts:
        raise ValueError("relative_path must identify an entry")
    current_fd = os.dup(root_fd)
    try:
        for index, part in enumerate(parts[:-1]):
            next_fd = _open_child_directory_unchecked(
                current_fd,
                part,
                path="/".join(parts[: index + 1]),
            )
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, parts[-1]
    except Exception:
        os.close(current_fd)
        raise


def _open_child_directory_unchecked(parent_fd: int, name: str, *, path: str) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
    )
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise RepositoryScanRaceError(
            f"repository directory became unavailable or changed type: {path}"
        ) from exc


def _manifest_identity(
    manifest: tuple[_ManifestItem, ...],
) -> tuple[tuple[str, RepositoryEntryKind, _StatSignature], ...]:
    return tuple((item.path, item.kind, item.signature) for item in manifest)


def _stat_signature(info: os.stat_result) -> _StatSignature:
    return _StatSignature(
        mode=int(info.st_mode),
        device=int(info.st_dev),
        inode=int(info.st_ino),
        size=int(info.st_size),
        rdev=int(info.st_rdev),
        mtime_ns=int(info.st_mtime_ns),
        ctime_ns=int(info.st_ctime_ns),
    )


def _identity_signature(info: os.stat_result) -> tuple[int, int, int]:
    return int(info.st_dev), int(info.st_ino), stat.S_IFMT(info.st_mode)


def _is_executable(mode: int) -> bool:
    return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def _classify_content(size: int, sample: bytes) -> RepositoryContentKind:
    if size == 0:
        return RepositoryContentKind.EMPTY
    if b"\x00" in sample:
        return RepositoryContentKind.BINARY
    controls = sum(
        1
        for byte in sample
        if (byte < 32 and byte not in {8, 9, 10, 12, 13}) or byte == 127
    )
    if sample and controls / len(sample) > 0.05:
        return RepositoryContentKind.BINARY
    return RepositoryContentKind.TEXT


__all__ = [
    "RepositoryScanPolicy",
    "RepositoryScanner",
    "detect_language",
]
