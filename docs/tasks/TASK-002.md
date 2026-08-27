# TASK-002 — Hard Budget Governor

Status: `PASS — implemented, deeply reviewed, and hardened`.

## Objective

Prevent paid model/tool calls from being authorized when their bounded worst-case cost would push the personal paid-API budget above the configured €20 UTC-calendar-month ceiling.

## Core authorization invariant

A new paid call may be authorized only when:

`settled spend + active reservations + worst-case next-call cost <= durable monthly limit`

The check and reservation insertion execute inside the same SQLite `BEGIN IMMEDIATE` transaction so concurrent processes cannot oversubscribe the budget by racing on a stale balance.

## Scope

- Exact monetary representation using integer micro-euros in SQLite and `Decimal` at Python boundaries.
- Durable UTC monthly budget policy.
- Reservation-before-call authorization.
- `active -> settled`, `active -> cancelled`, and fail-closed `active -> breached` lifecycle.
- Idempotent reservation keys and deterministic settlement retries.
- Durable spend/reservation snapshots.
- Cross-process limit mismatch detection.
- DB-level ledger constraints and immutability triggers.
- Conservative rounding upward to one micro-euro.
- Concurrency protection with `BEGIN IMMEDIATE`.

## Deterministic guarantees

1. The governor never authorizes a reservation whose bounded worst-case cost makes `spent + reserved` exceed the durable period limit.
2. Concurrent reservation attempts serialize before they read budget state.
3. The first reservation in a UTC month persists the period limit. A process configured with a different limit fails closed for that period, including idempotent reservation retries.
4. Reservation identity, reserved amount, period policy, and terminal reservation states cannot be silently edited or deleted through normal SQLite writes because DB triggers reject those mutations.
5. Duplicate idempotency keys cannot create duplicate reservations.
6. Settlement with an actual cost above the pre-authorized worst-case amount is durably recorded as a breach and locks the period against new reservations.
7. Binary floating-point values are rejected at monetary API boundaries.
8. A blocked reservation raises a clear pre-call error stating that no paid call was authorized.

## Important guarantee boundary

The governor can guarantee authorization against the cost bound it is given. It cannot reverse an external provider charge that exceeds a supposedly valid worst-case estimate after the network call has already happened. Therefore provider adapters added later must compute conservative bounded cost estimates before authorization and must use provider-side output/token limits where available. If actual billing nevertheless exceeds the reservation, TASK-002 records the true cost, marks the reservation `breached`, and blocks further paid reservations for that UTC month.

## Schema

`budget_periods`
- `period_utc` primary key (`YYYY-MM`)
- immutable `limit_micros`
- creation timestamp

`budget_reservations`
- UUID reservation id
- globally unique idempotency key
- period/task/provider/model provenance
- immutable reserved amount
- actual amount once settled/breached
- lifecycle status and timestamps
- breach reason

CHECK constraints enforce valid state combinations. Triggers reject deletion or mutation of ledger identity/policy fields and make terminal reservation rows immutable.

## Review findings corrected

The mandatory post-implementation review found and corrected two additional consistency defects:

1. Idempotent reservation lookup originally returned an existing reservation before checking whether the current process used the same durable monthly limit. The retry path now enforces the durable period policy as well.
2. Initial ledger triggers protected reservation identity and deletion but did not prevent a terminal `settled`, `cancelled`, or `breached` row from being rewritten into another otherwise-valid lifecycle state. Migration `003_budget_terminal_immutability.sql` now rejects every update to a terminal reservation row.

The review also verified the existing protections for conservative monetary rounding, SQLite integer-range overflow, period rollover, duplicate idempotency conflicts, actual-cost breach handling, immutable period policy, and concurrent oversubscription.

## Verification evidence

The reviewed implementation was reconstructed in the isolated execution environment and verified with:

- 22/22 budget-specific and adversarial pytest tests passing.
- Explicit two-thread concurrent-reservation race testing demonstrating that only one `€0.75` reservation can succeed against a `€1.00` ceiling.
- Regression tests for durable-limit enforcement on idempotent lookup and terminal-row immutability.
- SQLite migration execution through migrations `001`, `002`, and `003`.
- Python bytecode compilation of the reviewed `src/` and task-specific test files.
- Repository diff inspection confirming TASK-002 changes are limited to budget/database/task-rule files.
- Default `master` verification confirming it remains at the original commit `30c655a94e7da96a9ccdbe40db2a6ea10adc2174`.

`ruff` and `mypy` remain configured development gates but were not available in the isolated execution runtime. They are therefore explicitly not claimed as executed PASS evidence.

## Result

No unresolved correctness or architectural blocker remains within the scope of TASK-002. Provider-specific cost estimators and provider-call wiring are intentionally deferred to later tasks; those components must consume this governor before any paid network call is possible.
