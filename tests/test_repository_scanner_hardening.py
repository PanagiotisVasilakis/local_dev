from pathlib import Path

import pytest

from local_dev.repository import (
    RepositoryContentKind,
    RepositoryScanError,
    RepositoryScanPolicy,
    RepositoryScanner,
)


def _write(root: Path, relative: str, content: bytes | str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


def test_gitignored_directory_is_pruned_before_entry_budget(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, ".gitignore", "dist/\n")
    _write(root, "keep.py", "print('keep')\n")
    for index in range(20):
        _write(root, f"dist/generated-{index}.py", "x\n")

    snapshot = RepositoryScanner(RepositoryScanPolicy(max_entries=3)).scan(root.resolve())

    assert [entry.path for entry in snapshot.entries] == [".gitignore", "keep.py"]


def test_directory_entries_count_toward_traversal_budget(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a").mkdir()
    (root / "b").mkdir()

    with pytest.raises(RepositoryScanError, match="more than 1"):
        RepositoryScanner(RepositoryScanPolicy(max_entries=1)).scan(root.resolve())


def test_unicode_bom_text_is_not_misclassified_as_binary(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, "utf16.txt", "hello\n".encode("utf-16"))
    _write(root, "utf32.txt", "world\n".encode("utf-32"))

    snapshot = RepositoryScanner().scan(root.resolve())
    kinds = {entry.path: entry.content_kind for entry in snapshot.entries}

    assert kinds == {
        "utf16.txt": RepositoryContentKind.TEXT,
        "utf32.txt": RepositoryContentKind.TEXT,
    }
