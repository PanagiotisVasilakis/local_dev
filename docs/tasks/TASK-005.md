# TASK-005 — Deterministic Repository Scanner & Repo Map

Status: `REVIEW FIXES IMPLEMENTED — FINAL VERIFICATION PENDING`.

## Objective

Create the deterministic repository-observation layer that later retrieval, AST/symbol indexing,
context compilation, and agent planning can trust.

TASK-005 does not perform semantic retrieval, AST parsing, embeddings, model prompting, or tool
execution. It only answers: "what repository entries exist, what are their stable identities and
basic classifications, and how did the snapshot change?"

## Scope

- Recursive repository scanning from an explicit absolute root.
- Secure POSIX directory traversal using directory file descriptors and `O_NOFOLLOW`.
- No traversal through file or directory symlink targets.
- Stable repository-relative POSIX paths.
- Streaming SHA-256 for regular files.
- Symlink identity based on link-target bytes rather than target contents.
- Safe representation of special filesystem entries without opening them.
- Text / binary / empty classification using a bounded content sample.
- Deterministic language detection from file name / suffix.
- Repository-local `.gitignore` selection.
- Root-independent, mtime-independent snapshot fingerprint.
- Deterministic snapshot comparison: added, removed, content-modified, metadata-only.
- Stable textual repo-map rendering with escaped paths.
- Explicit race detection when repository state changes during snapshot construction.

## Security and consistency invariants

1. The scanner never follows a repository symlink target to obtain file contents.
2. Directory traversal uses `dir_fd`, `O_DIRECTORY`, and `O_NOFOLLOW`; intermediate-directory
   symlink replacement fails closed.
3. Platforms lacking the required secure directory-descriptor primitives are rejected rather than
   using a weaker path-based fallback.
4. Regular files are opened relative to the already-open repository descriptor and are checked
   against the manifest's device/inode/mode/size/mtime/ctime signature before reading.
5. The file signature is checked again after hashing.
6. The selected manifest is collected a second time after hashing. A mismatch raises
   `RepositoryScanRaceError` rather than emitting an authoritative mixed-state snapshot.
7. Ignore files are themselves opened with the same no-follow regular-file path and have a bounded
   size.
8. FIFOs, sockets, devices, and other special entries are never opened for content.
9. Built-in metadata/cache/dependency names such as `.git`, `node_modules`, `.venv`, and
   `__pycache__` are excluded before traversal and can be replaced through an explicit scan policy.
10. A maximum raw-entry count prevents an accidentally unbounded scan.

## Determinism

The snapshot SHA-256 includes only repository-relative entry semantics:

- normalized relative path
- entry kind
- content/link/special digest
- size
- executable bit
- content classification
- language classification

It intentionally excludes the absolute repository root, inode/device numbers, mtimes, and scan
time. Therefore equivalent repository contents can produce the same fingerprint in different
checkout locations.

Filesystem timestamps and inode/device values are still used internally for race detection but are
not part of the public deterministic fingerprint.

## Ignore semantics

TASK-005 reads repository-local `.gitignore` files only. It intentionally does not read global Git
configuration or `.git/info/exclude`, because those are workstation-specific and would make
snapshots differ between machines.

The matcher implements the common repository-local Git ignore semantics needed by the scanner:
comments, escaped leading `#`/`!`, negation, anchored patterns, directory-only patterns, `*`, `?`,
character classes, `**`, and nested `.gitignore` scope. An ignored parent directory remains ignored
unless the parent itself is re-included, matching Git's important parent-directory rule.

This task does not claim byte-for-byte equivalence with every pathological Git ignore escaping edge
case. If future evals expose a material incompatibility, the matcher can be replaced behind the
same scanner contract without changing snapshot consumers.

## Repo map

`render_repo_map()` produces a stable text representation containing:

- repository basename
- snapshot fingerprint
- entry/byte counts
- language counts
- one sorted escaped record per repository entry

The map is intentionally structural, not semantic. Later retrieval layers decide which files or
symbols belong in model context.


## Deep-review findings corrected

The separate committed-code review identified and corrected the following issues:

1. The initial path contract rejected valid POSIX filenames containing literal backslashes or
   leading/trailing whitespace. Repository paths now preserve those legal filename bytes while
   still rejecting absolute paths, `.` / `..` traversal components, NUL, and non-normalized `/`
   structure.
2. POSIX filenames decoded through `surrogateescape` could fail fingerprint serialization when
   they contained non-UTF-8 bytes. Fingerprint and repo-map JSON escaping now uses ASCII-safe
   escapes so arbitrary filesystem bytes remain representable.
3. An active `.gitignore` could ignore its own pathname, leaving snapshot selection dependent on a
   control file absent from the snapshot. Active ignore control files are now retained even when
   their own file rule would exclude them; ignore files below an ignored parent remain inactive.
4. Intermediate-directory symlink replacement was hardened with directory-descriptor traversal
   rather than only protecting the final component.
5. Special-entry identity now includes the semantic device identifier (`st_rdev`) without leaking
   checkout-specific inode/device identity into the public fingerprint.
6. Malformed ignore patterns that cannot be represented safely by the deterministic matcher are
   surfaced as `RepositoryScanError` instead of leaking a raw regular-expression exception.
7. Ambiguous `file_count` naming was replaced with explicit `entry_count` and
   `regular_file_count`.

## Verification gate

Before this task becomes `PASS`, the committed implementation must receive a separate deep review
and adversarial verification pass under the standing project rules. Findings must be corrected and
the final candidate re-run before integration into `master`.
