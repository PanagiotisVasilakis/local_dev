# local_dev

Local-first agentic software-engineering system focused on high-quality code, evidence-backed reasoning, persistent local memory, and a deterministic monthly API budget cap.

## Current status

`TASK-001` (repository foundation), `TASK-002` (hard budget governor), `TASK-003` (provider abstraction and paid-call boundary), `TASK-004` (local model runtime), `TASK-005` (deterministic repository scanner and repo map), `TASK-006` (deterministic lexical / FTS retrieval index), and `TASK-007` (deterministic structural / AST symbol and import index) are complete, deeply reviewed, and integrated into the canonical `master` branch. The integrated foundation-through-TASK-004 baseline passed `AUDIT-001`, and TASK-005 subsequently passed the post-integration `AUDIT-002` hardening review. No real paid model provider is connected yet.

## Design principles

- Local-first storage and retrieval.
- Deterministic orchestration around probabilistic models.
- Evidence from repository state and executed tools is authoritative; model summaries are not.
- A verification result cannot be marked `PASSED` without a typed, immutable evidence reference set.
- Every paid call must pass through the application-owned paid-call gateway and hard budget governor before provider transport can execute.
- Provider-call journal state and budget-reservation state are durably cross-checked by SQLite invariants.
- Monetary values used for budget enforcement are represented exactly, never as binary floats.
- Ambiguous provider-dispatch outcomes fail closed: budget remains reserved and replay is blocked.
- Local-model HTTP execution is restricted to numeric loopback endpoints; environment proxies and HTTP redirects are disabled for that transport.
- Repository observation is deterministic and fail-closed: secure no-follow traversal, stable content fingerprints, explicit race detection, and repository-local ignore rules precede retrieval/indexing.
- Lexical retrieval is snapshot-bound and fail-closed: indexed bytes must match repository evidence, stale/inconsistent durable state is rejected, and ranking is deterministic within the repository.
- Structural retrieval is snapshot-bound and fail-closed: Python definitions/imports are AST-derived, compile-validity checked, durably coherence-checked, and never presented as a call/reference graph.
- Boundary contracts validate security/accounting-relevant runtime types instead of relying on type annotations alone.
- Modular monolith first; split services only when measurements justify it.
- Runtime dependencies are added only when they provide material value.

## Branch workflow

`master` is the canonical integration branch. New work is implemented on task-scoped branches and is merged/fast-forwarded into `master` only after implementation plus the mandatory deep-review/adversarial-verification gate have passed.

## Development

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
mypy src
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `LOCAL_DEV_DATA_DIR` | `~/.local/share/local-dev` | Local persistent state directory |
| `LOCAL_DEV_DATABASE_PATH` | `<data-dir>/local_dev.db` | SQLite database path |
| `LOCAL_DEV_MONTHLY_BUDGET_EUR` | `20` | Hard monthly paid-API ceiling |
| `LOCAL_DEV_LOG_LEVEL` | `INFO` | Structured logging level |

`TASK-002` enforces the budget ceiling durably in SQLite using reservation-before-call authorization. `TASK-003` adds the provider-neutral `PaidCallGateway`: future paid provider adapters must quote locally, reserve budget, durably enter dispatch state, execute transport, and then settle/cancel/hold accounting through this boundary.

`AUDIT-001` adds durable provider/reservation coherence checks and hardens deterministic runtime contracts and UTC budget accounting without changing the core architecture.

`TASK-004` adds the vendor-neutral local model runtime plus an OpenAI-compatible loopback-only HTTP implementation. It does not select or install a specific inference engine or model; runtime/model selection and prompt compilation are later concerns.

`TASK-005` adds a deterministic POSIX repository scanner and structural repo map. `AUDIT-002` additionally hardens ignore-aware subtree pruning and Unicode BOM text classification. Repository traversal remains no-follow, content-addressed, race-detecting, and suitable as an evidence source for downstream indexing.

`TASK-006` adds the deterministic lexical retrieval layer. It binds reads to TASK-005 snapshots, stores bounded line chunks in SQLite FTS5, performs atomic incremental synchronization, validates durable file/chunk/FTS state before retrieval, reports skipped or lossy coverage explicitly, and applies repository-local deterministic ranking after lexical candidate selection. Migration `007_lexical_retrieval.sql` carries the durable index schema.

`TASK-007` adds the deterministic structural retrieval layer. It binds structural state to repository snapshots, parses Python through the stdlib AST using Python-3.12 grammar constraints plus non-executing compile validation, records authoritative definitions/imports with deterministic identities and lexical scopes, performs atomic incremental synchronization, validates durable parent/scope/file/digest coherence, and reports unsupported or parse-failed coverage explicitly. It intentionally does not claim call-graph or reference resolution. Migration `008_structural_index.sql` carries the durable structural schema.

No real paid provider SDK or paid network integration has been added yet.

## Database migrations

Migration files are bundled under `src/local_dev/migrations/`, are append-only, and are named `NNN_name.sql`. Applied migrations are tracked by numeric version, filename, and SHA-256 checksum. Renaming, editing, deleting, or inserting an older migration fails closed. Pending migrations are applied in one explicit SQLite transaction; migration files must not contain their own transaction-control statements.
