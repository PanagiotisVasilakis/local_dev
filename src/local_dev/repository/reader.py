from __future__ import annotations

import errno
import hashlib
import os
import stat
from pathlib import PurePosixPath

from local_dev.repository.contracts import (
    RepositoryEntry,
    RepositoryEntryKind,
    RepositoryScanError,
    RepositoryScanRaceError,
    RepositorySnapshot,
)


def read_snapshot_file(
    snapshot: RepositorySnapshot,
    path: str,
    *,
    max_bytes: int,
) -> bytes:
    """Read one regular file and prove the bytes still match the supplied snapshot."""

    if not isinstance(snapshot, RepositorySnapshot):
        raise TypeError("snapshot must be RepositorySnapshot")
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty repository-relative string")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")

    entry = _find_entry(snapshot, path)
    if entry.kind is not RepositoryEntryKind.FILE:
        raise ValueError("snapshot path must refer to a regular file")
    if entry.size_bytes > max_bytes:
        raise RepositoryScanError(
            f"snapshot file exceeds the {max_bytes}-byte read limit: {entry.path}"
        )

    _require_secure_open_support()
    root_fd = _open_root(snapshot)
    try:
        parent_fd, name = _open_parent(root_fd, entry.path)
        try:
            descriptor = _open_regular(parent_fd, name, entry.path)
            try:
                current = os.fstat(descriptor)
                if not stat.S_ISREG(current.st_mode):
                    raise RepositoryScanRaceError(
                        f"snapshot path is no longer a regular file: {entry.path}"
                    )
                payload = _read_bounded(descriptor, max_bytes, entry.path)
                after = os.fstat(descriptor)
                if (
                    current.st_dev != after.st_dev
                    or current.st_ino != after.st_ino
                    or current.st_size != after.st_size
                    or current.st_mtime_ns != after.st_mtime_ns
                    or current.st_ctime_ns != after.st_ctime_ns
                ):
                    raise RepositoryScanRaceError(
                        f"repository file changed while reading: {entry.path}"
                    )
            finally:
                os.close(descriptor)
        finally:
            os.close(parent_fd)
    finally:
        os.close(root_fd)

    digest = hashlib.sha256(payload).hexdigest()
    if len(payload) != entry.size_bytes or digest != entry.sha256:
        raise RepositoryScanRaceError(
            f"repository file no longer matches the supplied snapshot: {entry.path}"
        )
    return payload


def _find_entry(snapshot: RepositorySnapshot, path: str) -> RepositoryEntry:
    for entry in snapshot.entries:
        if entry.path == path:
            return entry
    raise KeyError(f"snapshot does not contain repository path: {path}")


def _require_secure_open_support() -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
        or os.open not in os.supports_dir_fd
    ):
        raise RepositoryScanError(
            "secure snapshot-bound file reads require POSIX dir_fd and O_NOFOLLOW support"
        )


def _open_root(snapshot: RepositorySnapshot) -> int:
    root = snapshot.repository_root
    try:
        before = os.lstat(root)
    except OSError as exc:
        raise RepositoryScanError("repository root is unavailable") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise RepositoryScanRaceError("repository root changed after snapshot creation")

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise RepositoryScanRaceError("repository root changed before file read") from exc

    current = os.fstat(descriptor)
    if current.st_dev != before.st_dev or current.st_ino != before.st_ino:
        os.close(descriptor)
        raise RepositoryScanRaceError("repository root changed before file read")
    return descriptor


def _open_parent(root_fd: int, path: str) -> tuple[int, str]:
    pure = PurePosixPath(path)
    parts = pure.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("path must be a normalized repository-relative path")

    current_fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            try:
                child_fd = os.open(component, flags, dir_fd=current_fd)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
                    raise RepositoryScanRaceError(
                        f"repository directory changed before file read: {path}"
                    ) from exc
                raise RepositoryScanError(
                    f"failed to open repository directory while reading: {path}"
                ) from exc
            os.close(current_fd)
            current_fd = child_fd
        return current_fd, parts[-1]
    except Exception:
        os.close(current_fd)
        raise


def _open_regular(parent_fd: int, name: str, path: str) -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
            raise RepositoryScanRaceError(
                f"repository file changed before reading: {path}"
            ) from exc
        raise RepositoryScanError(f"failed to open repository file: {path}") from exc


def _read_bounded(descriptor: int, max_bytes: int, path: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - total))
        except OSError as exc:
            raise RepositoryScanError(f"failed to read repository file: {path}") from exc
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise RepositoryScanRaceError(
                f"repository file grew beyond the allowed read size: {path}"
            )
    return b"".join(chunks)
