# TASK-003 — Provider abstraction and paid-call boundary

Status: `IMPLEMENTED — REVIEW GATE PENDING`.

## Objective

Create the single application-owned execution boundary for future paid model-provider calls. No real provider SDK or network integration is added in this task.

## Core flow

`ModelRequest -> local CostQuote -> BudgetGovernor.reserve -> durable PREPARED -> durable DISPATCHING -> adapter.execute -> settle/cancel/hold`

A provider transport is invoked only after its worst-case price has been quoted locally, the budget reservation has succeeded, and the provider-call journal has durably transitioned to `dispatching`.

## Scope

- Provider-neutral cost quote and response contracts.
- Provider adapter protocol and registry.
- `PaidCallGateway` as the official paid execution path.
- Durable provider-call journal keyed by idempotency key and budget reservation.
- Request+quote SHA-256 fingerprinting.
- Explicit call lifecycle:
  - `prepared -> dispatching -> completed`
  - `prepared -> dispatching -> not_sent`
  - `prepared -> dispatching -> uncertain`
- Replay prevention after dispatch starts.
- Conservative failure handling integrated with TASK-002.

## Deterministic guarantees

1. `adapter.execute()` is not called when the budget reservation is rejected.
2. Dispatch does not begin until `dispatching` is durably committed.
3. Once a call is `dispatching`, the same durable call cannot transition back to `prepared`.
4. `completed`, `not_sent`, and `uncertain` are terminal database states.
5. A generic provider exception is treated as potentially billable: the reservation remains held and replay is blocked.
6. A reservation is released only when the adapter raises `ProviderNotSentError`, which is an explicit contract that billable transport was never reached.
7. Same-key concurrent callers cannot both transition the same durable call from `prepared` to `dispatching`; therefore the gateway dispatches it at most once.
8. Invalid or mismatched provider responses do not release budget. They become `uncertain`.
9. Actual cost settlement still goes through TASK-002. An actual cost above the pre-authorized bound becomes a budget breach and locks the period.

## Crash-safety model

The system prefers possible temporary over-reservation to duplicate provider charges.

- Crash before the durable `dispatching` transition: a `prepared` call may be resumed safely.
- Crash after `dispatching` but before a trusted terminal record: retry is blocked because the network-send outcome may be unknowable.
- Crash after budget settlement but before the provider-call journal reaches `completed`: the budget reservation is terminal, so TASK-002 also blocks a new authorization with the same idempotency key.

Later reconciliation tooling may recover conservative `dispatching/uncertain` records using provider-side request IDs or billing evidence. TASK-003 intentionally does not guess.

## Important boundary

This is an application architecture guarantee, not a Python sandbox. Arbitrary future code could deliberately import a vendor SDK and bypass the gateway. Therefore real provider adapters added later must live behind this boundary, and later repository-policy checks should reject direct paid-provider transport imports/calls outside approved adapter modules.

`CostQuote` is also required by contract to be local/non-billable. A provider-specific implementation that performs a paid network request while quoting would violate the adapter contract and must fail its task review.

## Schema

Migration `004_provider_call_boundary.sql` creates `provider_calls` with:

- unique call id
- globally unique idempotency key
- one-to-one reservation foreign key
- request/quote fingerprint
- provider/model identity
- lifecycle status
- dispatch/terminal timestamps
- non-sensitive error type only

Database triggers reject deletion, identity mutation, and invalid lifecycle transitions.

## Verification gate

Before this task becomes `PASS`, the standing project rule requires a separate committed-code review, adversarial verification, corrections for any findings, and a final re-run. Unavailable lint/type/build gates must be reported rather than inferred.
