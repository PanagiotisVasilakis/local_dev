# AUDIT-001 — Foundation through TASK-004

Status: `HARDENING IMPLEMENTED — FINAL REVIEW PENDING`.

## Objective

Review the integrated `master` state after TASK-001 through TASK-004 as one system rather than as isolated task deliverables. The audit focuses on deterministic guarantees, cross-module assumptions, durable accounting, runtime contracts, local-network isolation, and integration readiness before repository indexing work begins.

## Baseline

- Canonical branch: `master`
- Audited baseline SHA: `e7fc62ef0c28e3c840e6ca84d4970d7ee21b8751`
- TASK-004 is already integrated; its task branch is an ancestor of `master`.
- No real paid provider adapter is connected.
- Runtime dependency list remains empty.

## Findings requiring correction

### 1. Verification status type bypass

`VerificationResult` required evidence only when `status is VerificationStatus.PASSED`, but did not runtime-check the status type. A caller could therefore construct `status="passed"` and bypass the evidence requirement despite the annotation.

The contract now requires a real `VerificationStatus` and defensively normalizes/validates evidence as an immutable tuple of `EvidenceRef` values.

### 2. Mutable or untyped deterministic contracts

Several boundary contracts relied on Python annotations for UUID/integer/enum validity. This allowed values such as booleans or floats in token limits and non-UUID task identifiers to survive construction. In a paid path, an invalid task identifier could have been serialized into the durable budget ledger and fail only when read back.

Core and local-runtime request contracts now validate deterministic scalar types at runtime. Paid budget authorization also validates task/reservation UUIDs and string identities before persistence.

### 3. UTC month-boundary clock split

Budget reservation previously obtained its UTC period and creation timestamp through two independent calls to the clock when `now` was omitted. A call spanning an exact UTC month boundary could therefore associate the period with one month and the timestamp with the next.

Reservation authorization now observes the clock once and derives both values from the same UTC instant. Falsy non-datetime values are rejected rather than silently replaced by the current time.

### 4. Provider-call / reservation coherence was application-only

The provider journal had a foreign key to a reservation, but SQLite did not verify that the duplicated idempotency key, provider, and model matched that reservation or that it was active at call preparation time.

Append-only migration `005_provider_reservation_coherence.sql` adds a fail-closed insert trigger enforcing those relationships in durable state.

### 5. Local transport public injection weakened the loopback claim

`OpenAICompatibleLocalRuntime` publicly accepted an arbitrary transport implementation. That was useful for tests but made the public loopback-only configuration guarantee weaker than its documentation. The default transport was also exported and could be called directly with an arbitrary URL.

The production constructor no longer accepts a transport override. Tests replace the private transport after construction. `UrllibLocalHttpTransport` now independently validates every URL as numeric loopback before opening it.

## Non-blocking observations

- `master` currently has no GitHub branch protection and no remote commit status checks. Repository correctness therefore depends on the documented task-branch/review process rather than server-enforced CI gates.
- No cloud CI is introduced by this audit. The project remains local-first and does not add runtime dependencies.
- Conservative provider `dispatching`/`uncertain` records still require a future reconciliation workflow. This is intentional fail-closed behavior already documented in TASK-003, not an audit regression.
- The durable budget limit is immutable within an established UTC period. An explicit same-month policy-change workflow does not yet exist; failure is conservative rather than permissive.

## Verification gate

This audit is not complete until the committed hardening receives an independent review and adversarial re-verification. Only then may it be integrated into `master` and used as the baseline for TASK-005.
