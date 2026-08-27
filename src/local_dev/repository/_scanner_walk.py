from __future__ import annotations

import os
import stat

from local_dev.repository import _scanner_base as _base
from local_dev.repository.contracts import RepositoryEntryKind, RepositoryScanError
from local_dev.repository.ignore import RepositoryIgnoreRules


def collect_selected(
    scanner: _base.RepositoryScanner, root_fd: int
) -> tuple[_base._ManifestItem, ...]:
    items: list[_base._ManifestItem] = []
    visited = [0]
    _walk(scanner, root_fd, root_fd, "", (), items, visited)
    return tuple(sorted(items, key=lambda item: item.path))


def _walk(
    scanner: _base.RepositoryScanner,
    root_fd: int,
    directory_fd: int,
    relative_directory: str,
    ignore_sources: tuple[tuple[str, str], ...],
    items: list[_base._ManifestItem],
    visited: list[int],
) -> None:
    try:
        with os.scandir(directory_fd) as iterator:
            directory_entries = sorted(iterator, key=lambda entry: entry.name)
    except OSError as exc:
        label = relative_directory or "."
        raise RepositoryScanError(f"failed to enumerate repository directory: {label}") from exc

    prepared: list[tuple[os.DirEntry[str], str, os.stat_result]] = []
    for entry in directory_entries:
        if entry.name in scanner._policy.excluded_names:
            continue
        visited[0] += 1
        if visited[0] > scanner._policy.max_entries:
            raise RepositoryScanError(
                f"repository contains more than {scanner._policy.max_entries} scannable entries"
            )
        relative = entry.name if not relative_directory else f"{relative_directory}/{entry.name}"
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise RepositoryScanError(f"failed to stat repository entry: {relative}") from exc
        prepared.append((entry, relative, info))

    active_sources = _active_sources(scanner, root_fd, prepared, ignore_sources)
    try:
        matcher = RepositoryIgnoreRules.from_sources(active_sources)
    except ValueError as exc:
        raise RepositoryScanError("repository contains an invalid .gitignore pattern") from exc

    for entry, relative, info in prepared:
        mode = info.st_mode
        if stat.S_ISDIR(mode):
            if matcher.is_ignored(relative, is_dir=True):
                continue
            child_fd = _open_child_directory(
                directory_fd,
                entry.name,
                expected=_base._stat_signature(info),
                path=relative,
            )
            try:
                _walk(scanner, root_fd, child_fd, relative, active_sources, items, visited)
            finally:
                os.close(child_fd)
            continue

        if stat.S_ISREG(mode):
            kind = RepositoryEntryKind.FILE
        elif stat.S_ISLNK(mode):
            kind = RepositoryEntryKind.SYMLINK
        else:
            kind = RepositoryEntryKind.SPECIAL
        active_ignore = entry.name == ".gitignore" and stat.S_ISREG(mode)
        if not active_ignore and matcher.is_ignored(relative, is_dir=False):
            continue
        items.append(
            _base._ManifestItem(
                path=relative,
                kind=kind,
                signature=_base._stat_signature(info),
            )
        )


def _active_sources(
    scanner: _base.RepositoryScanner,
    root_fd: int,
    prepared: list[tuple[os.DirEntry[str], str, os.stat_result]],
    inherited: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    for entry, relative, info in prepared:
        if entry.name != ".gitignore" or not stat.S_ISREG(info.st_mode):
            continue
        item = _base._ManifestItem(
            path=relative,
            kind=RepositoryEntryKind.FILE,
            signature=_base._stat_signature(info),
        )
        content = scanner._read_regular_file_bytes(
            root_fd,
            item,
            max_bytes=scanner._policy.max_ignore_file_bytes,
            purpose="ignore file",
        )
        return inherited + ((relative, content.decode("utf-8", errors="surrogateescape")),)
    return inherited


def _open_child_directory(
    parent_fd: int,
    name: str,
    *,
    expected: _base._StatSignature,
    path: str,
) -> int:
    # Resolve through the public module so race-injection tests can replace the hook.
    from local_dev.repository import scanner as public_scanner

    return public_scanner._open_child_directory(
        parent_fd, name, expected=expected, path=path
    )
