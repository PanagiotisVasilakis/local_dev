# local_dev

Local-first agentic software-engineering system focused on high-quality code, evidence-backed reasoning, persistent local memory, and a deterministic monthly API budget cap.

## Current status

`TASK-001` establishes the repository foundation only. No paid model provider is connected yet.

## Design principles

- Local-first storage and retrieval.
- Deterministic orchestration around probabilistic models.
- Evidence from repository state and executed tools is authoritative; model summaries are not.
- Paid calls must eventually pass through a hard budget governor.
- Modular monolith first; split services only when measurements justify it.
- Runtime dependencies are added only when they provide material value.

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
| `LOCAL_DEV_MONTHLY_BUDGET_EUR` | `20` | Future hard monthly API ceiling |
| `LOCAL_DEV_LOG_LEVEL` | `INFO` | Structured logging level |

The €20 value is configuration only in TASK-001. Enforcement is implemented in TASK-002; no paid provider calls should be added before that governor exists.
