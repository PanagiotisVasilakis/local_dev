import os
import stat
from pathlib import Path

import pytest

from local_dev.repository import (
    RepositoryContentKind,
    RepositoryEntryKind,
    RepositoryScanError,
    RepositoryScanPolicy,
    RepositoryScanRaceError,
    RepositoryScanner,
    compare_snapshots,
    detect_language,
    render_repo_map,
)


def _write(root: Path, relative: str, content: bytes | str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)
    return path


def test_scan_is_deterministic_and_sorted(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, "z.txt", "z")
    _write(root, "a.py", "print('a')\n")
    _write(root, "nested/m.md", "# m\n")

    snapshot = RepositoryScanner().scan(root.resolve())

    assert [entry.path for entry in snapshot.entries] == ["a.py", "nested/m.md", "z.txt"]
    assert snapshot.file_count == 3
    assert snapshot.language_counts == {"Markdown": 1, "Python": 1}
    rescanned = RepositoryScanner().scan(root.resolve())
    assert snapshot.fingerprint_sha256 == rescanned.fingerprint_sha256


def test_fingerprint_is_independent_of_root_and_mtime(tmp_path: Path) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    for root in (first, second):
        _write(root, "src/app.py", "print('same')\n")
        _write(root, "README.md", "same\n")
    os.utime(second / "src/app.py", ns=(1_000_000_000, 2_000_000_000))

    scanner = RepositoryScanner()
    one = scanner.scan(first.resolve())
    two = scanner.scan(second.resolve())

    assert one.fingerprint_sha256 == two.fingerprint_sha256
    assert [(e.path, e.sha256, e.executable) for e in one.entries] == [
        (e.path, e.sha256, e.executable) for e in two.entries
    ]


def test_content_change_is_reported_as_modified(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    path = _write(root, "a.py", "one\n")
    scanner = RepositoryScanner()
    before = scanner.scan(root.resolve())
    path.write_text("two\n", encoding="utf-8")
    after = scanner.scan(root.resolve())

    diff = compare_snapshots(before, after)
    assert diff.modified == ("a.py",)
    assert not diff.added
    assert not diff.removed
    assert before.fingerprint_sha256 != after.fingerprint_sha256


def test_add_remove_and_executable_metadata_diff(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    script = _write(root, "run.sh", "#!/bin/sh\n")
    removed = _write(root, "old.txt", "old\n")
    scanner = RepositoryScanner()
    before = scanner.scan(root.resolve())

    removed.unlink()
    _write(root, "new.txt", "new\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    after = scanner.scan(root.resolve())

    diff = compare_snapshots(before, after)
    assert diff.added == ("new.txt",)
    assert diff.removed == ("old.txt",)
    assert diff.metadata_only == ("run.sh",)


def test_binary_empty_and_text_classification(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, "text.txt", "hello\n")
    _write(root, "empty.txt", b"")
    _write(root, "blob.bin", b"\x00\x01\x02")
    snapshot = RepositoryScanner().scan(root.resolve())
    by_path = {entry.path: entry for entry in snapshot.entries}

    assert by_path["text.txt"].content_kind is RepositoryContentKind.TEXT
    assert by_path["empty.txt"].content_kind is RepositoryContentKind.EMPTY
    assert by_path["blob.bin"].content_kind is RepositoryContentKind.BINARY


@pytest.mark.parametrize(
    ("path", "language"),
    [
        ("a.py", "Python"),
        ("a.tsx", "TypeScript"),
        ("Dockerfile.dev", "Dockerfile"),
        ("Makefile", "Makefile"),
        ("schema.sql", "SQL"),
        ("README.md", "Markdown"),
        ("data.unknown", None),
    ],
)
def test_language_detection(path: str, language: str | None) -> None:
    assert detect_language(path) == language


def test_builtin_metadata_and_dependency_directories_are_excluded(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, ".git/config", "secret-ish metadata")
    _write(root, "node_modules/pkg/index.js", "ignored")
    _write(root, ".venv/lib/x.py", "ignored")
    _write(root, "src/app.py", "kept")

    snapshot = RepositoryScanner().scan(root.resolve())
    assert [entry.path for entry in snapshot.entries] == ["src/app.py"]


def test_root_gitignore_common_patterns_and_negation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(
        root,
        ".gitignore",
        "*.log\n"
        "!keep.log\n"
        "generated/\n"
        "/rootonly.txt\n"
        "docs/*.tmp\n"
        "**/secret.txt\n",
    )
    _write(root, "drop.log", "x")
    _write(root, "keep.log", "x")
    _write(root, "generated/a.py", "x")
    _write(root, "rootonly.txt", "x")
    _write(root, "sub/rootonly.txt", "x")
    _write(root, "docs/a.tmp", "x")
    _write(root, "docs/deeper/a.tmp", "x")
    _write(root, "sub/secret.txt", "x")
    _write(root, "keep.py", "x")

    paths = [entry.path for entry in RepositoryScanner().scan(root.resolve()).entries]

    assert ".gitignore" in paths
    assert "keep.log" in paths
    assert "sub/rootonly.txt" in paths
    assert "docs/deeper/a.tmp" in paths
    assert "keep.py" in paths
    assert "drop.log" not in paths
    assert "generated/a.py" not in paths
    assert "rootonly.txt" not in paths
    assert "docs/a.tmp" not in paths
    assert "sub/secret.txt" not in paths


def test_nested_gitignore_is_scoped_to_its_directory(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, "sub/.gitignore", "*.tmp\n")
    _write(root, "sub/a.tmp", "ignored")
    _write(root, "other/a.tmp", "kept")

    paths = [entry.path for entry in RepositoryScanner().scan(root.resolve()).entries]
    assert "sub/a.tmp" not in paths
    assert "other/a.tmp" in paths
    assert "sub/.gitignore" in paths


def test_ignored_parent_cannot_be_reincluded_only_by_child_negation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, ".gitignore", "generated/\n!generated/keep.txt\n")
    _write(root, "generated/keep.txt", "still ignored by git semantics")

    paths = [entry.path for entry in RepositoryScanner().scan(root.resolve()).entries]
    assert "generated/keep.txt" not in paths


def test_symlink_target_is_not_followed_outside_repository(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    _write(external, "secret.txt", "do-not-read")
    os.symlink(external / "secret.txt", root / "link.txt")
    os.symlink(external, root / "linkdir")

    snapshot = RepositoryScanner().scan(root.resolve())
    by_path = {entry.path: entry for entry in snapshot.entries}

    assert set(by_path) == {"link.txt", "linkdir"}
    assert by_path["link.txt"].kind is RepositoryEntryKind.SYMLINK
    assert by_path["linkdir"].kind is RepositoryEntryKind.SYMLINK
    assert all("secret.txt" not in entry.path for entry in snapshot.entries)


def test_special_fifo_is_recorded_without_opening(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO unsupported")
    root = tmp_path / "repo"
    root.mkdir()
    fifo = root / "pipe"
    os.mkfifo(fifo)

    snapshot = RepositoryScanner().scan(root.resolve())
    entry = snapshot.entries[0]
    assert entry.path == "pipe"
    assert entry.kind is RepositoryEntryKind.SPECIAL
    assert entry.content_kind is RepositoryContentKind.NOT_APPLICABLE


def test_scan_detects_filesystem_change_during_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, "a.txt", "a")
    changing = _write(root, "b.txt", "b")
    scanner = RepositoryScanner()
    original = scanner._hash_regular_file
    mutated = False

    def wrapped(root_fd, item):  # type: ignore[no-untyped-def]
        nonlocal mutated
        result = original(root_fd, item)
        if item.path == "a.txt" and not mutated:
            changing.write_text("changed", encoding="utf-8")
            mutated = True
        return result

    monkeypatch.setattr(scanner, "_hash_regular_file", wrapped)
    with pytest.raises(RepositoryScanRaceError, match="changed"):
        scanner.scan(root.resolve())


def test_scan_rejects_symlink_repository_root(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(real, link)

    with pytest.raises(RepositoryScanError, match="must not be a symlink"):
        RepositoryScanner().scan(link.absolute())


def test_scan_rejects_relative_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        RepositoryScanner().scan(Path("relative"))


def test_entry_limit_fails_closed_before_unbounded_scan(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, "a", "a")
    _write(root, "b", "b")

    scanner = RepositoryScanner(RepositoryScanPolicy(max_entries=1))
    with pytest.raises(RepositoryScanError, match="more than 1"):
        scanner.scan(root.resolve())


def test_repo_map_is_stable_and_escapes_unusual_paths(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, "a\tb.py", "x")
    snapshot = RepositoryScanner().scan(root.resolve())

    rendered = render_repo_map(snapshot)
    assert rendered == render_repo_map(snapshot)
    assert '"a\\tb.py"' in rendered
    assert snapshot.fingerprint_sha256 in rendered


def test_intermediate_directory_symlink_swap_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import local_dev.repository.scanner as scanner_module

    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    victim = root / "victim"
    victim.mkdir()
    _write(victim, "inside.txt", "inside")
    _write(external, "outside.txt", "outside")

    original_open_child = scanner_module._open_child_directory
    swapped = False

    def guarded_open_child(parent_fd, name, *, expected, path):  # type: ignore[no-untyped-def]
        nonlocal swapped
        if name == "victim" and not swapped:
            moved = root / "victim-original"
            victim.rename(moved)
            os.symlink(external, victim)
            swapped = True
        return original_open_child(parent_fd, name, expected=expected, path=path)

    monkeypatch.setattr(scanner_module, "_open_child_directory", guarded_open_child)
    with pytest.raises(RepositoryScanRaceError, match="changed type"):
        RepositoryScanner().scan(root.resolve())


def test_oversized_gitignore_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, ".gitignore", "x" * 32)
    scanner = RepositoryScanner(RepositoryScanPolicy(max_ignore_file_bytes=8))

    with pytest.raises(RepositoryScanError, match="ignore file exceeds"):
        scanner.scan(root.resolve())


def test_repo_map_and_fingerprint_reject_unsorted_external_entries(tmp_path: Path) -> None:
    from local_dev.repository import RepositoryEntry, repository_fingerprint

    digest = "0" * 64
    first = RepositoryEntry(
        path="b.txt",
        kind=RepositoryEntryKind.FILE,
        size_bytes=1,
        sha256=digest,
        executable=False,
        content_kind=RepositoryContentKind.TEXT,
    )
    second = RepositoryEntry(
        path="a.txt",
        kind=RepositoryEntryKind.FILE,
        size_bytes=1,
        sha256=digest,
        executable=False,
        content_kind=RepositoryContentKind.TEXT,
    )
    with pytest.raises(ValueError, match="sorted"):
        repository_fingerprint((first, second))
