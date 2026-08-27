# TASK-004 — Local model runtime

Status: `IMPLEMENTED — REVIEW GATE PENDING`.

## Objective

Provide a vendor-neutral, non-billable local model execution boundary that can later power prompt/context compilation and local reasoning without involving paid-provider infrastructure.

## Scope

- Immutable local chat request/response contracts.
- Explicit local runtime health state.
- Vendor-neutral `LocalModelRuntime` protocol.
- Concrete OpenAI-compatible HTTP runtime using only the Python standard library.
- Local-only transport enforcement.
- Bounded response size and request timeout.
- Strict JSON/protocol validation.
- No paid-provider integration and no budget reservation for local inference.

## Local-only network invariant

The concrete HTTP runtime accepts only numeric loopback endpoints:

- IPv4 loopback (`127.0.0.0/8`)
- IPv6 loopback (`::1`)

DNS hostnames such as `localhost`, LAN/private addresses, credentials in URLs, query strings, fragments, and HTTPS endpoints are rejected.

The default HTTP transport also:

1. disables environment-configured HTTP proxies,
2. refuses HTTP redirects,
3. enforces a configured maximum response size,
4. maps timeout/unreachable conditions into explicit local-runtime errors.

These constraints prevent a supposedly local model runtime from silently becoming external network egress through DNS, proxy configuration, or redirects.

## Request model

`LocalGenerationRequest` contains:

- task UUID
- requested model
- ordered chat messages
- positive maximum output-token bound
- temperature
- optional seed
- immutable string metadata

Metadata is local orchestration context and is deliberately not forwarded to the model HTTP endpoint.

## Response model

`LocalGenerationResponse` records:

- runtime name
- requested model
- model name reported by the runtime
- textual output
- optional finish reason
- optional token usage

The runtime requires non-empty textual output for this task because tool-call/multimodal responses are not yet part of the supported local contract.

## Health semantics

- `READY`: runtime is reachable, protocol-valid, and advertises at least one model.
- `DEGRADED`: runtime is reachable but response/protocol is invalid or no models are advertised.
- `UNAVAILABLE`: timeout or transport reachability failure.

Health checks do not claim that a particular model will successfully generate; that remains a generation-time fact.

## Failure taxonomy

- `LocalRuntimeUnavailable`
- `LocalRuntimeTimeout`
- `LocalRuntimeProtocolError`

Unexpected programming errors are not converted into health/network errors.

## Deterministic guarantees

1. The concrete runtime cannot be configured to send model traffic to a non-loopback host.
2. Environment HTTP proxy variables cannot redirect its default transport.
3. HTTP redirects are never followed.
4. Responses larger than the configured byte cap are rejected.
5. Generation is non-streaming in TASK-004 and has a positive caller-supplied output-token bound.
6. Invalid JSON, malformed model lists, malformed choices, empty textual content, and inconsistent token totals fail explicitly.
7. Orchestration metadata is not leaked into the local model request payload.
8. No real paid model provider or paid network path is introduced by this task.

## Important boundary

This runtime is an application contract, not an operating-system sandbox. Deliberately malicious future code could still create unrelated network sockets. Repository policy/tool sandboxing will address broader process/network permissions in later tasks.

`OpenAICompatibleLocalRuntime` is intentionally compatible with local servers that expose the common `/v1/models` and `/v1/chat/completions` API shape. TASK-004 does not select or install a specific inference engine or model.

## Review gate

Before this task becomes `PASS`, the committed implementation must receive the standing independent deep-review/adversarial-verification pass. Findings must be corrected and re-tested before integration into `master`.
