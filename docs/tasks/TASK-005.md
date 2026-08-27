# TASK-005 — Deterministic Repository Scanner & Repo Map

Status: `PASS — implemented, deeply reviewed, and hardened`.

## Objective

Create the deterministic repository-observation layer that later retrieval, AST/symbol indexing,
context compilation, and agent planning can trust.

TASK-005 does not perform semantic retrieval, AST parsing, embeddings, model prompting, or tool
execution. It answers: "what repository entries exist, what are their stable identities and basic
classifications, and how did the snapshot change?"

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
   against the manifest device/inode/mode/size/mtime/ctime signature before reading.
5. The file signature is checked again after hashing.
6. The selected manifest is collected a second time after hashing. A mismatch raises
   `RepositoryScanRaceError` rather than emitting an authoritative mixed-state snapshot.
7. Ignore files are themselves opened through the same no-follow regular-file path and have a
   bounded size.
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
time. Equivalent repository contents can therefore produce the same fingerprint in different
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
case. The matcher is replaceable behind the same scanner contract if future evals expose a material
incompatibility.

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
   control file absent from the snapshot. Active ignore control files are retained even when their
   own file rule would exclude them; ignore files below an ignored parent remain inactive.
4. Intermediate-directory symlink replacement was hardened with directory-descriptor traversal
   rather than only protecting the final component.
5. Special-entry identity now includes the semantic device identifier (`st_rdev`) without leaking
   checkout-specific inode/device identity into the public fingerprint.
6. Malformed ignore patterns that cannot be represented safely by the deterministic matcher are
   surfaced as `RepositoryScanError` instead of leaking a raw regular-expression exception.
7. Ambiguous `file_count` naming was replaced with explicit `entry_count` and
   `regular_file_count`.
8. Final committed-source verification caught an escaping mutation in the non-UTF-8 regression
   test itself. The test literal was corrected and its GitHub blob SHA was verified to exactly
   match the locally executed file before this task was closed.

## Verification performed

The hardened final candidate received a separate adversarial verification pass after the review
fixes:

- 30/30 TASK-005 scanner tests passed on the exact corrected candidate.
- Tests covered deterministic ordering/fingerprints, add/remove/content/metadata diffs,
  text/binary/empty classification, language detection, built-in exclusions, nested ignore rules,
  ignored-parent semantics, symlinks, special files, scan races, entry limits, and map rendering.
- Adversarial tests covered an intermediate-directory symlink swap, repository mutation during
  hashing, oversized `.gitignore`, malformed ignore patterns, self-ignored active `.gitignore`,
  legal POSIX whitespace/backslash names, and non-UTF-8 filenames.
- A differential corpus was checked against Git 2.47.3 for common `.gitignore` semantics including
  anchoring, negation, directory rules, `**`, `?`, character classes, escaped `#`/`!`, escaped
  spaces, and nested ignore files; the selected file sets matched exactly for that corpus.
- Python bytecode compilation passed.
- Python 3.12 grammar parsing passed.
- A 100-character source-line gate passed for the TASK-005 candidate.
- A wheel was built without dependencies/build isolation, verified to contain the complete
  `local_dev.repository` package, installed into a clean target, and the installed scanner produced
  a valid deterministic snapshot in a fresh repository.
- Production scanner files were compared by Git blob SHA with the committed branch, and the final
  corrected test file was also verified by exact Git blob SHA.
- Repository compare confirmed the task branch is a clean descendant of the audited `master` and
  contains no unrelated budget/provider/local-runtime changes.

## Verification limitations

The execution container cannot resolve `github.com`, so a fresh literal clone and a repo-wide
`pytest` invocation against a Git checkout were not available. TASK-005 verification used the exact
committed production blobs plus the exact corrected test candidate in an isolated reconstruction;
a full-checkout repo-wide PASS is not claimed.

`ruff` and `mypy` remain configured development gates but were unavailable in the isolated runtime,
so they are not claimed as executed PASS evidence.

## Result

No known correctness, repository-confinement, deterministic-fingerprint, symlink-traversal, or
common-ignore-semantics blocker remains within TASK-005 scope. The scanner/repo-map layer is ready
to serve as the deterministic source for the next lexical retrieval/indexing layer.
