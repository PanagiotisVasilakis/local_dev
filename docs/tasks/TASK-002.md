# TASK-002 — Hard Budget Governor

Status: `IMPLEMENTED — REVIEW GATE PENDING`.

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
3. The first reservation in a UTC month persists the period limit. A process configured with a different limit fails closed for that period.
4. Reservation identity, reserved amount, and period policy cannot be silently edited or deleted through normal SQLite writes because DB triggers reject those mutations.
5. Duplicate idempotency keys cannot create duplicate reservations.
6. Settlement with an actual cost above the pre-authorized worst-case amount is durably recorded as a breach and locks the period against new reservations.
7. Binary floating-point values are rejected at monetary API boundaries.

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

CHECK constraints enforce valid state combinations, and triggers reject deletion or mutation of ledger identity/policy fields.

## Review gate

Before changing this document to `PASS`, the task must undergo the standing deep-review rule in `docs/PROJECT_RULES.md`: inspect the committed implementation independently, run adversarial tests, correct findings, re-run verification, and report any unavailable gates explicitly.
