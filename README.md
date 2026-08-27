# local_dev

Local-first agentic software-engineering system focused on high-quality code, evidence-backed reasoning, persistent local memory, and a deterministic monthly API budget cap.

## Current status

`TASK-001` (repository foundation) and `TASK-002` (hard budget governor) are complete, reviewed, and integrated into the canonical `master` branch. No paid model provider is connected yet.

## Design principles

- Local-first storage and retrieval.
- Deterministic orchestration around probabilistic models.
- Evidence from repository state and executed tools is authoritative; model summaries are not.
- A verification result cannot be marked `PASSED` without an evidence reference.
- Every paid call must pass through the hard budget governor before it can be authorized.
- Monetary values used for budget enforcement are represented exactly, never as binary floats.
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

`TASK-002` enforces the budget ceiling durably in SQLite using reservation-before-call authorization. No paid provider adapter should be added unless all paid requests are routed through that boundary.

## Database migrations

Migration files are bundled under `src/local_dev/migrations/`, are append-only, and are named `NNN_name.sql`. Applied migrations are tracked by numeric version, filename, and SHA-256 checksum. Renaming, editing, deleting, or inserting an older migration fails closed. Pending migrations are applied in one explicit SQLite transaction; migration files must not contain their own transaction-control statements.
