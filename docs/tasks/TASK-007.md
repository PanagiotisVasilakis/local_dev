# TASK-007 — Deterministic Structural / AST Index

Status: **PASS — implemented, deeply reviewed, and hardened**

## Goal

Add a deterministic structural repository layer on top of the snapshot-bound file reader from TASK-005/006. The layer records authoritative source definitions and imports without claiming heuristic reference or call-graph resolution.

## Scope delivered

- Language-neutral durable structural contracts and index API.
- Authoritative Python parsing using the stdlib AST with the grammar constrained to Python 3.12 syntax.
- Runtime-bound policy fingerprint so parser/compiler/runtime changes invalidate durable state instead of silently reusing it.
- Non-executing `compile()` validation after AST parsing because `ast.parse()` alone does not perform all compile/scoping validity checks.
- Deterministic symbols for classes, functions, async functions, methods, async methods, module/class assignment variables, and Python 3.12 type aliases.
- Nested qualified names, parent identities, decorators, deterministic function/class signatures, positional-only/default/keyword-only/variadic parameters, and PEP-695 type parameters where represented by the indexed definition contract.
- Deterministic imports with plain/from-import semantics, aliases, relative levels, lexical scope identities, source positions, and ordinal identity.
- AST source positions stored as Python AST UTF-8 byte offsets.
- Snapshot-bound file reads through the existing secure repository reader; no direct unconstrained path reads.
- Explicit per-file coverage states: indexed, parse_error, unsupported_language, unsupported_content, skipped_size.
- Atomic incremental synchronization under `BEGIN IMMEDIATE`.
- Stale-snapshot and policy mismatch detection.
- Repository-local deterministic identities and multi-repository isolation.
- Durable count, file-set, file-SHA, parent/scope, row and digest coherence checks.
- Append-only migration `008_structural_index.sql` with composite foreign keys, replace-only/immutable row policies, and parent/scope/file coherence triggers.
- Fail-closed result limits rather than silent truncation.
- Explicit resource bounds for file size, AST node count, symbols per file, imports per file, and query results.

## Deliberate boundary

TASK-007 indexes authoritative definitions/imports only. It does **not** claim call graph, reference resolution, inferred types, dynamic import resolution, or cross-language parsing. Those require a later graph/reference layer and must not be represented as deterministic evidence until their confidence/provenance contract exists.

Python parsing is runtime-bound. `feature_version=(3, 12)` constrains accepted grammar, while compile validation and AST unparsing use the running supported Python interpreter. The policy fingerprint includes interpreter implementation/version and structural format revision, so a runtime change forces index rebuild.

## Deep-review findings closed before PASS

The implementation/review cycle found and fixed, among other issues:

1. SQL query construction initially placed `LIMIT` before deterministic `ORDER BY`; fixed and regression-covered.
2. Plain-import durable semantics were made consistent: `import x` stores `module=x, name=x`; `from x import y` stores `module=x, name=y`.
3. Public structural dataclasses and policies gained runtime type/value validation instead of relying on annotations.
4. `ast.parse()` was insufficient for compile/scoping validity; non-executing `compile()` validation was added.
5. Python AST source positions are treated as UTF-8 byte offsets, matching the Python AST contract.
6. Function signature rendering was made deterministic for zero-argument, positional-only, default, variadic, keyword-only, async and PEP-695 type-parameter cases.
7. Destructuring assignment symbols use the individual target AST positions rather than the enclosing assignment span.
8. Python 3.12 type aliases inside named lexical scopes are indexed rather than being restricted to module/class scope.
9. Parser/compiler/traversal recursion and overflow failures are translated to typed structural failures.
10. Non-UTF-8 POSIX paths and parse-error messages are made SQLite/UTF-8 safe with deterministic escaping where required.
11. SQLite `CHECK`/NULL semantics were hardened explicitly for plain imports; composite-FK NULL behavior is not relied on as a completeness check.
12. Parent and import-scope qualified-name/kind coherence is enforced by triggers in addition to composite foreign keys.
13. Symbol/import file SHA coherence is enforced durably.
14. Durable malformed rows/state/path/JSON are translated fail-closed to `StructuralIndexError`.
15. Query paths are normalized repository-relative paths and oversized query result sets fail closed.
16. Symbol/import/AST resource-limit failures are regression-tested for transaction rollback; the prior durable snapshot remains queryable.
17. Missing AST end-position metadata fails closed rather than manufacturing positions.
18. Durable fingerprints/digests are validated as lowercase 64-character SHA-256 values, not merely length-64 strings.
19. Implementation files were modularized before commit so connector transport could be verified byte-for-byte; no mismatched blob was admitted to the committed tree.
20. The remaining internal asserts in `_node_span()` are not enforcement: explicit runtime integer validation occurs first, so optimized Python does not remove a safety check.

## Verification evidence

Final frozen implementation before commit:

- TASK-007 adversarial/regression suite: **39/39 PASS**.
- Python bytecode compilation: PASS.
- Python 3.12 grammar parsing: PASS.
- configured 100-character source/test line gate: PASS.
- migration `008` SQL completeness: PASS.
- canonical migrations `001→007` were reconstructed directly from the remote `master` and their local Git object hashes matched the canonical GitHub blob SHAs exactly.
- fresh real migration chain `001→008`: PASS.
- `PRAGMA foreign_key_check`: empty result.
- `PRAGMA integrity_check`: `ok`.
- wheel build: PASS.
- wheel contents include the structural package and migration `008`: PASS.
- clean wheel install: PASS.
- installed-package fresh `001→008` migration bootstrap: PASS.
- installed `StructuralIndex.sync()`, `symbols()` and `imports()` smoke: PASS.

Implementation commit:

- `924dd2ff4d857b188975004b7af52e0be71de45d` — `feat: add deterministic structural index`
- parent is exactly the pre-TASK-007 canonical `master` SHA `cf54031a6d82b95b1b16df9761d342a58bbcdf13`.
- committed diff is TASK-007-only: structural package, migration `008`, and structural regression tests.
- the committed tree points to the exact Git blob SHAs of the frozen files used by the verification gate, so the pre-commit executable evidence is byte-for-byte applicable to the committed implementation.

Post-commit review re-read the exact committed parser, collector, sync, query, durable-state and migration semantics. No unresolved correctness, repository-confinement, atomicity, stale-index, parser-validity, durable-coherence, identity, or deterministic-query blocker remained.

`ruff` and `mypy` were not installed in the isolated execution environment and are therefore **not** claimed as executed PASS gates.

## Result

TASK-007 satisfies its deterministic structural-index scope and is eligible for non-force fast-forward integration into canonical `master`.
