# TASK-001 — Repository foundation and domain contracts

Status: `PASS` after review hardening.

## Objective

Establish a minimal, production-oriented Python foundation for the local-first agentic development system before any paid provider integration exists.

## Scope

- Python 3.12+ `src/` package layout.
- Typed immutable-oriented domain contracts for task, model-request, evidence, and verification state.
- Fail-fast environment configuration.
- Exact decimal representation for the monthly API budget configuration.
- Structured JSON logging.
- SQLite boundary with WAL, foreign keys, explicit transaction semantics, and bundled append-only migrations.
- Test, lint, type-check, and packaging configuration.

## Non-goals

- Provider/API clients.
- Model routing.
- Budget reservation/accounting enforcement; that is TASK-002.
- Repository indexing, retrieval, memory, sandbox, or agent execution.
- UI.

## Invariants

1. No paid provider call path exists in TASK-001.
2. Monetary budget configuration is never represented as a binary floating-point number.
3. `VerificationResult(PASSED, ...)` cannot exist without at least one evidence reference.
4. Externally supplied metadata is defensively copied before entering frozen domain objects.
5. SQLite foreign keys are enabled and WAL mode is required.
6. Migration history is append-only: applied migration deletion, rename, or content mutation fails closed.
7. Pending migrations execute atomically under a write lock; a failure leaves none of that pending batch applied.
8. Migration files cannot control transaction boundaries themselves.
9. Concurrent migration starters serialize on SQLite and re-read migration state while holding the write lock.
10. The default schema migrations are shipped inside the installable Python package.

## Review findings corrected

- Replaced `float` budget configuration with `Decimal` and reject NaN/infinity/non-positive values.
- Reworked SQLite migration transaction handling so `executescript()` cannot silently defeat outer atomicity.
- Added deterministic migration naming/version rules, checksums, rename/delete/backfill detection, and concurrency locking.
- Added an SQLite authorizer that rejects transaction/savepoint control from migration SQL while still permitting trigger `BEGIN ... END` blocks.
- Made task/model metadata defensively immutable.
- Required evidence for a passed verification result.
- Changed JSON logging timestamps to use the original `LogRecord.created` event time.
- Updated package metadata to PEP 639 SPDX license syntax.
- Bundled migrations as package data so wheel installs retain their schema bootstrap.
- Tightened pytest configuration with strict config/markers and importlib import mode.

## Verification

The review build was independently reconstructed from the intended committed contents and verified with:

- 36 pytest tests covering configuration, contracts, SQLite success/failure/integrity/concurrency behavior, bundled migrations, and logging.
- Python bytecode compilation of `src/` and `tests/`.
- Python 3.12 grammar parsing of all Python sources.
- Wheel build without dependencies or build isolation.
- Wheel inspection confirming bundled SQL migrations and MIT license metadata/files.
- Fresh wheel-install smoke test that runs the bundled default migration and reads `schema_generation = 1`.

`ruff` and `mypy` are configured as required development gates, but the isolated review runtime did not have those binaries cached and had outbound package installation disabled. They therefore remain explicit local developer gates rather than falsely reported as executed evidence in this review.

## Next task

`TASK-002 — Hard Budget Governor`: exact monetary accounting, reservation-before-call authorization, durable spend ledger, and fail-closed enforcement of the €20 monthly ceiling.
