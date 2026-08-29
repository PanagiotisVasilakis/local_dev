# Local Dev Agent

Local-first, budget-governed agentic software engineering system.

## Current status

Completed and reviewed foundation:

- TASK-001 — repository foundation
- TASK-002 — hard Budget Governor
- TASK-003 — provider abstraction and paid-call boundary
- TASK-004 — local model runtime
- AUDIT-001 — integrated foundation review/hardening
- TASK-005 — deterministic repository scanner / repo map
- AUDIT-002 — TASK-005 post-integration hardening
- TASK-006 — deterministic lexical / FTS retrieval index
- TASK-007 — deterministic structural / AST symbol and import index

The current architecture provides a durable local-first base with deterministic repository observation, snapshot-bound lexical retrieval, and authoritative Python structural definitions/imports. Paid-provider execution remains budget-governed and separate from local repository evidence.

Canonical integration branch: `master`.

## Engineering invariants

- `master` is the canonical integration branch.
- Work is task-scoped and reviewed before integration.
- Deterministic guarantees live in code/tools, not prompts.
- Repository/tool evidence is authoritative; LLM prose alone is not evidence.
- Security, accounting, provenance, stale-state, and verification ambiguity fail closed.
- Monetary enforcement never uses binary floating point.
- Paid calls require durable Budget Governor authorization before transport.
- Structural/lexical indexes are bound to deterministic repository snapshots.
- Type annotations alone are not accepted as deterministic runtime enforcement at critical boundaries.

See `docs/PROJECT_RULES.md` and the task/audit records under `docs/` for the full contract and verification history.
