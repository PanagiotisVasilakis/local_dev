# TASK-006 — Deterministic Lexical / FTS Retrieval Index

Status: `PASS — implemented, deeply reviewed, and hardened`.

## Objective

Build a deterministic lexical retrieval layer on top of TASK-005 repository snapshots so later context compilation, structural indexing, planning, and agent execution can retrieve repository evidence without relying on model guesses.

TASK-006 adds lexical retrieval only. It does not add AST/symbol indexing, embeddings, semantic reranking, prompt compilation, or agent orchestration.

## Scope

- Snapshot-bound regular-file reads using POSIX `dir_fd` and `O_NOFOLLOW`.
- SHA-256 and size verification against the supplied repository snapshot before file bytes become index evidence.
- SQLite FTS5 lexical candidate retrieval.
- Deterministic line-based chunking with bounded file/chunk/query/candidate limits.
- Root-independent deterministic chunk identifiers.
- Atomic incremental synchronization under the existing SQLite `BEGIN IMMEDIATE` boundary.
- Durable snapshot/policy/index-state accounting and stale-index rejection.
- Deterministic repository-local integer ranking after FTS candidate selection.
- UTF-8, UTF-16, and UTF-32 BOM-aware decoding; lossy UTF-8 replacement is reported explicitly.
- Explicit coverage reporting for skipped oversized files and lossy-decoded files.
- Multi-repository isolation within the same SQLite database.

## Deterministic and fail-closed guarantees

1. Indexed regular-file bytes must still match the supplied TASK-005 snapshot by size and SHA-256.
2. Intermediate and final symlink substitution is rejected by the snapshot reader rather than followed.
3. Index synchronization is atomic: a failed changed-file read or index update rolls back to the previous durable index state.
4. Search requires the durable snapshot fingerprint and policy/index compatibility fingerprint to match the supplied snapshot/runtime.
5. Durable lexical file metadata, global counts, per-file chunk counts, and the read-only FTS vocabulary digest are checked before search results are returned.
6. Returned chunk content is rehashed and its deterministic chunk ID is recomputed before it becomes retrieval evidence.
7. Raw user FTS syntax is not executed directly. Queries are converted into bounded quoted lexical terms.
8. Queries exceeding the deterministic candidate cap fail closed and require refinement instead of silently truncating evidence.
9. FTS5 is used only for candidate selection. Authoritative ranking is repository-local and deterministic, so indexing an unrelated repository cannot alter another repository's result ordering.
10. Oversized or lossy files are surfaced in `skipped_paths` / `lossy_paths`; incomplete lexical coverage is never silently presented as complete.

## Durable schema

Migration `007_lexical_retrieval.sql` creates:

- `lexical_index_state`
- `lexical_files`
- `lexical_chunks`
- external-content `lexical_chunks_fts`
- `lexical_chunks_fts_vocab`
- insert/delete synchronization triggers for FTS
- replace-only triggers for lexical file/chunk metadata

Chunk uniqueness is scoped by repository (`UNIQUE(repository_id, chunk_id)`), allowing identical checkouts to share one SQLite database without identity collisions.

## Deep-review findings corrected

The implementation and committed-code review corrected the following material issues before PASS:

1. Initial globally unique chunk IDs collided when two identical checkouts shared one database; uniqueness is now repository-scoped.
2. Early isolation tests used a stub scanner and simplified migrations; final verification uses the real TASK-005 scanner and the real migration chain `001→007`.
3. FTS5 BM25 ranking depended on global shared-table statistics; it was removed from authoritative ordering and replaced with deterministic repository-local scoring.
4. FTS5 `integrity-check` is a special write operation and was unsuitable for every read path; full integrity validation remains in atomic sync while search uses a read-only vocabulary digest.
5. The durable policy fingerprint originally omitted implementation/runtime compatibility; it now includes explicit index-format revision, SQLite version, Unicode database version, and policy values.
6. `lexical_files` allowed in-place mutation; migration 007 makes file metadata replace-only.
7. Unicode repository paths were initially escaped before FTS indexing, preventing natural path queries such as `cafe` from matching `café_module.py`; normal Unicode paths are now indexed directly while surrogate filesystem paths use an explicit reversible escape form.
8. Search semantics were clarified: a snapshot fingerprint proves consistency with the supplied snapshot; callers must rescan to establish live filesystem freshness.
9. Broad queries previously risked implicit candidate truncation; they now fail closed above the configured candidate bound.
10. Large GitHub connector payloads could alter/truncate test materialization. Final production and regression files were committed only after Git object SHA checks matched the locally executed bytes; the final branch tree was inspected to confirm those exact blobs are referenced.

## Verification performed

Implementation checkpoint: `49ca2626f87f5de1415b46ef409108b25401990f`.

Exact committed regression checkpoint: `258939c72388836b0c5621acd6283de2fcaad2c8`.

The final candidate received a separate committed-code review and adversarial verification pass:

- `141/141` integrated tests passed with the final split TASK-006 regression modules and the previous project regression suite.
- The obsolete monolithic TASK-006 test file and temporary non-committed test artifacts were removed from the final verification workspace before the 141-test run.
- Tests cover incremental sync, no-op sync, removals, rollback, same-repository concurrency, concurrent read/write behavior, multi-repository isolation, identical-checkout chunk identity, candidate limits, deterministic ranking, Unicode/diacritic matching, UTF-16/UTF-32 end-to-end retrieval, non-UTF-8 filenames, symlink swaps, oversized/lossy coverage, direct SQLite metadata/count tampering, FTS tampering, stale snapshots, query bounds, and migration absence.
- Python bytecode compilation passed.
- Python 3.12 grammar parsing passed for `src` and `tests`.
- A wheel was built with `pip wheel --no-deps --no-build-isolation`.
- Wheel contents were verified to include `local_dev.repository.reader`, the complete `local_dev.retrieval` package, and migration `007_lexical_retrieval.sql`.
- The wheel was installed into a clean target without dependencies.
- Fresh installed-database bootstrap applied migrations `[1, 2, 3, 4, 5, 6, 7]`.
- Installed `PRAGMA integrity_check` returned `ok`.
- Installed `RepositoryScanner -> LexicalIndex.sync -> LexicalIndex.search` smoke passed.
- GitHub compare confirmed the task branch is a clean descendant of the reviewed TASK-005 master baseline and contains only TASK-006 retrieval/schema/test work.

## Verification limitations

The isolated runtime does not provide `ruff` or `mypy`, so those configured development gates are not claimed as executed PASS evidence.

Direct fresh `git clone` from the execution container remains unavailable because that container cannot resolve `github.com`; repository provenance was instead checked through the GitHub connector, exact Git blob SHAs, branch/tree inspection, and local execution of those exact source bytes.

## Result

No known unresolved correctness, snapshot-confinement, atomicity, stale-index, deterministic-ranking, multi-repository-isolation, FTS-consistency, Unicode-path, or bounded-query blocker remains within TASK-006 scope. The lexical retrieval layer is ready to serve as the deterministic lexical candidate source for the next structural/AST indexing layer.
