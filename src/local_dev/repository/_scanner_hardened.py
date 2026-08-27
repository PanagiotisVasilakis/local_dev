from local_dev.repository import _scanner_base as _base
from local_dev.repository._content_classification import classify_content
from local_dev.repository._scanner_walk import collect_selected
from local_dev.repository.contracts import RepositoryEntry, RepositoryEntryKind


class RepositoryScanner(_base.RepositoryScanner):
    """Scanner with ignore-aware subtree pruning and BOM-aware text classification."""

    def _collect_raw_manifest(self, root_fd: int) -> tuple[_base._ManifestItem, ...]:
        return collect_selected(self, root_fd)

    def _entry_from_manifest(
        self, root_fd: int, item: _base._ManifestItem
    ) -> RepositoryEntry:
        if item.kind is not RepositoryEntryKind.FILE:
            return super()._entry_from_manifest(root_fd, item)
        digest, sample = self._hash_regular_file(root_fd, item)
        return RepositoryEntry(
            path=item.path,
            kind=item.kind,
            size_bytes=item.signature.size,
            sha256=digest,
            executable=_base._is_executable(item.signature.mode),
            content_kind=classify_content(item.signature.size, sample),
            language=_base.detect_language(item.path),
        )
