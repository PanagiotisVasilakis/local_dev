# AUDIT-002 — TASK-005 post-integration hardening

Status: `HARDENING IMPLEMENTED — COMMITTED REVIEW PENDING`.

## Baseline

- Canonical branch before this audit: `master`.
- Baseline SHA: `d9d81628bf8c40b11587a725dc383bced0aa5aeb`.
- TASK-005 was already integrated and its production blobs matched the previously executed candidate.

## Additional findings corrected

### 1. Ignored directories were traversed before ignore selection

The scanner previously collected the complete raw manifest before applying repository-local
`.gitignore` rules. A large ignored directory such as `dist/` or `build/` could therefore consume
substantial traversal work or exhaust `max_entries` even though Git semantics excluded the entire
subtree.

Traversal now loads the active `.gitignore` for each entered directory before descending into its
children. A directory that is ignored by the active parent rules is pruned without opening or
enumerating the ignored subtree. Nested `.gitignore` files become active only after their parent
directory is admitted, preserving Git's ignored-parent rule.

The traversal budget now counts directory entries as well as non-directory entries in directories
that are actually visited, so a repository composed of huge directory fan-out cannot bypass the
entry bound. Built-in excluded names such as `.git` and `node_modules` are pruned before consuming
that budget.

### 2. BOM-identified Unicode text was classified as binary

The bounded text/binary heuristic treated any NUL byte as binary. UTF-16 and UTF-32 source/config
files with a byte-order mark therefore became `BINARY` and were unavailable to downstream lexical
retrieval despite being explicitly encoded text.

UTF-16 and UTF-32 BOM prefixes are now recognized as text before the generic NUL-byte heuristic.
TASK-006 decodes these encodings losslessly when their BOM is present.

## Verification

- 33/33 TASK-005 scanner tests passed after the additional hardening.
- A regression repository with `dist/` ignored and a traversal budget too small for the ignored
  subtree scans successfully without descending into `dist/`.
- Directory-only fan-out is counted by the traversal safety budget.
- UTF-16 and UTF-32 BOM files are classified as `TEXT`.
- The existing symlink, race, special-file, non-UTF-8 filename, deterministic fingerprint, and
  `.gitignore` regression suite remained green.
- A differential `.gitignore` corpus was re-run against Git 2.47.3 after traversal pruning; selected
  file sets matched exactly for the exercised common semantics.
- The integrated TASK-001 through TASK-006 reconstruction subsequently passed 140/140 tests using
  the real TASK-005 scanner and real migrations 001 through 007.

## Limitations

The execution container still cannot resolve `github.com`, so verification is performed on exact
GitHub-fetched/locally hashed source material rather than a fresh network clone. `ruff` and `mypy`
are configured gates but are not installed in the isolated runtime and are not claimed as PASS.

## Verdict

No known TASK-005 correctness, confinement, traversal-budget, common `.gitignore`, Unicode-BOM
classification, or deterministic-snapshot blocker remains after this audit.
