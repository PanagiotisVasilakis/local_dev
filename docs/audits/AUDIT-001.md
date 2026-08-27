# AUDIT-001 — Foundation through TASK-004

Status: `PASS — integrated baseline deeply reviewed and hardened`.

## Objective

Review the integrated `master` state after TASK-001 through TASK-004 as one system rather than as isolated task deliverables. The audit focuses on deterministic guarantees, cross-module assumptions, durable accounting, runtime contracts, local-network isolation, and integration readiness before repository indexing work begins.

## Baseline

- Canonical branch: `master`
- Audited baseline SHA: `e7fc62ef0c28e3c840e6ca84d4970d7ee21b8751`
- TASK-004 was already integrated before this audit; its task branch was confirmed as an ancestor of `master`.
- No real paid provider adapter is connected.
- Runtime dependency list remains empty.

## Findings corrected

### 1. Verification status type bypass

`VerificationResult` required evidence only when `status is VerificationStatus.PASSED`, but did not runtime-check the status type. A caller could therefore construct `status="passed"` and bypass the evidence requirement despite the annotation.

The contract now requires a real `VerificationStatus` and defensively normalizes/validates evidence as an immutable tuple of `EvidenceRef` values.

### 2. Mutable or untyped deterministic contracts

Several boundary contracts relied on Python annotations for UUID/integer/enum validity. This allowed values such as booleans or floats in token limits and non-UUID task identifiers to survive construction. In a paid path, an invalid task identifier could have been serialized into the durable budget ledger and fail only when read back.

Core, local-runtime, and paid-provider boundary contracts now validate deterministic scalar types at runtime. Paid budget authorization also validates task/reservation UUIDs and string identities before persistence.

### 3. UTC month-boundary clock split

Budget reservation previously obtained its UTC period and creation timestamp through two independent calls to the clock when `now` was omitted. A call spanning an exact UTC month boundary could therefore associate the period with one month and the timestamp with the next.

Reservation authorization now observes the clock once and derives both values from the same UTC instant. Falsy non-datetime values are rejected rather than silently replaced by the current time.

### 4. Provider-call / reservation coherence was application-only

The provider journal had a foreign key to a reservation, but SQLite did not verify that the duplicated idempotency key, provider, and model matched that reservation or that it was active at call preparation time.

Append-only migration `005_provider_reservation_coherence.sql` adds a fail-closed insert trigger enforcing those relationships in durable state.

A second review then identified lifecycle coherence as a separate concern. Append-only migration `006_provider_lifecycle_coherence.sql` validates existing journal/ledger pairs and adds transition guards so provider terminal states cannot contradict reservation state, and a reservation linked to a provider call cannot become terminal unless that call is in `dispatching`.

The `ProviderNotSentError` path now cancels the reservation before recording the `not_sent` journal state. If the terminal journal write then fails, replay remains blocked by the terminal budget reservation and the conservative `dispatching` row can be reconciled later.

### 5. Local transport public injection weakened the loopback claim

`OpenAICompatibleLocalRuntime` publicly accepted an arbitrary transport implementation. That was useful for tests but made the public loopback-only configuration guarantee weaker than its documentation. The default transport was also exported and could be called directly with an arbitrary URL.

The production constructor no longer accepts a transport override. Tests replace the private transport after construction. `UrllibLocalHttpTransport` now independently validates every URL as numeric loopback before opening it.

### 6. Paid-provider contracts relied on annotations for boundary typing

Provider quote/response identities, gateway request values, adapter names, and metadata could fail with incidental Python attribute errors rather than explicit boundary validation when supplied with malformed runtime values.

The paid-provider contracts and gateway now reject malformed runtime types before budget or provider state is mutated.

## Verification performed

The final hardened candidate received a separate adversarial verification pass after the review fixes:

- 61/61 integrated reconstructed tests passed. The suite covered audit regressions, TASK-002 budget behavior, TASK-003 gateway compatibility/lifecycle behavior, and TASK-004 local-runtime behavior.
- Concurrent budget authorization and same-key paid-call dispatch remained fail-closed.
- Provider success, over-budget, provider-not-sent, uncertain/replay, and accounting-breach paths were exercised with migrations 001 through 006 active.
- Direct SQL attempts to create provider/budget lifecycle contradictions were rejected by SQLite integrity triggers.
- A simulated UTC month rollover verified that reservation period and creation timestamp derive from one clock observation.
- Real in-process loopback HTTP checks continued to reject redirects and oversized responses; direct non-loopback transport URLs were rejected before socket use.
- Python bytecode compilation passed for the reconstructed source/tests.
- Python 3.12 grammar parsing passed for the reconstructed source/tests.
- The SQLite migration chain applied successfully in order: 001, 002, 003, 004, 005, 006.
- A wheel was built from the reconstructed package configuration, verified to contain migrations 005/006, installed into a clean target, and successfully bootstrapped a new database through all six migrations.
- Repository compare confirmed the audit branch remained a clean descendant of the audited `master` with no unrelated file changes.

## Verification limitations

The isolated execution container could not resolve `github.com`, so a fresh full repository clone and literal repo-wide test invocation against a checkout were not available. The audit therefore used GitHub-fetched committed source plus an isolated reconstruction of the affected integrated stack. This limitation is explicit; a full-checkout PASS is not claimed.

`ruff` and `mypy` are configured development gates but were not installed in the isolated runtime, so they are not claimed as executed PASS evidence.

## Non-blocking observations / planned debt

- `master` currently has no GitHub branch protection and no remote commit status checks. Repository correctness therefore depends on the documented task-branch/review process rather than server-enforced CI gates.
- No cloud CI is introduced by this audit. The project remains local-first and does not add runtime dependencies.
- Conservative provider `dispatching`/`uncertain` records still require a future reconciliation workflow. This is intentional fail-closed behavior already documented in TASK-003, not an audit regression.
- The durable budget limit is immutable within an established UTC period. An explicit same-month policy-change workflow does not yet exist; failure is conservative rather than permissive.
- The current paid and local model request contracts are intentionally different and still text-oriented. A unified richer conversation/tool-call contract should be designed when routing/agent tool use is implemented, before real paid-provider adapters are considered production-ready.

## Architecture verdict

The current direction remains sound: deterministic state/accounting boundaries are established first, local inference is isolated from paid-provider execution, and repository understanding can now be added without weakening those guarantees. No known unresolved correctness, accounting, or local-network blocker remains within the TASK-001 through TASK-004 scope.

The next architectural step remains the deterministic repository scanner/repo-map layer. Retrieval, AST/symbol indexing, semantic retrieval, routing, agent tooling, reconciliation, and richer model/tool-call contracts remain future tasks rather than missing prerequisites for repository scanning.
