# TASK-004 — Local model runtime

Status: `PASS — implemented, deeply reviewed, and hardened`.

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
4. maps timeout/unreachable conditions into explicit local-runtime errors,
5. maps malformed HTTP framing into an explicit protocol error.

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

Message collections are defensively normalized to an immutable tuple. Metadata is copied into an immutable mapping and is deliberately not forwarded to the model HTTP endpoint.

## Response model

`LocalGenerationResponse` records:

- runtime name
- requested model
- model name reported by the runtime
- textual output
- optional finish reason
- typed `LocalUsage`

The runtime requires non-empty textual output for this task because tool-call/multimodal responses are not yet part of the supported local contract.

## Health semantics

- `READY`: runtime is reachable, protocol-valid, and advertises at least one model.
- `DEGRADED`: runtime is reachable but response/protocol is invalid or no models are advertised.
- `UNAVAILABLE`: timeout or transport reachability failure.

The health contract itself rejects contradictory states such as `READY` with zero models or `UNAVAILABLE` while advertising models.

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
6. Invalid JSON, malformed model lists, malformed choices, empty textual content, inconsistent token totals, and malformed HTTP fail explicitly.
7. Orchestration metadata is not leaked into the local model request payload.
8. Mutable caller-owned message collections cannot mutate a frozen request after construction.
9. No real paid model provider or paid network path is introduced by this task.

## Review findings corrected

The mandatory post-implementation review identified and corrected:

1. A frozen `LocalGenerationRequest` could still retain a caller-supplied mutable message list at runtime despite its tuple annotation. Messages are now defensively normalized into a tuple.
2. `LocalGenerationResponse` did not runtime-check that `usage` was a `LocalUsage` object. The response contract now fails closed on an invalid usage type.
3. Health objects could be manually constructed with contradictory status/model combinations. The contract now rejects those states.
4. Malformed HTTP framing could escape the explicit local-runtime failure taxonomy as a raw `HTTPException`. It is now mapped to `LocalRuntimeProtocolError`.
5. The pre-commit security pass identified that Python's default urllib opener can inherit environment proxy settings. The concrete transport now installs an empty `ProxyHandler` and refuses redirects.

## Verification evidence

The reviewed implementation was exercised with:

- 23/23 task-specific pytest tests passing.
- Real in-process loopback HTTP tests verifying redirect refusal.
- Real in-process loopback HTTP tests verifying response-size enforcement.
- Endpoint rejection tests for HTTPS, DNS hostnames, LAN/public IPs, URL credentials, query strings, and fragments.
- Health parsing/degraded/unavailable tests.
- Generation payload and response validation tests.
- Regression tests for defensive message normalization and typed usage.
- Python 3.12 grammar parsing of all TASK-004 Python source/tests.
- Python bytecode compilation during review before the final cleanup.
- Repository diff inspection confirming TASK-004 changes are confined to the local-model package, task tests, and task documentation.

`ruff` and `mypy` are configured repository gates but are not installed in the isolated execution runtime, so they are explicitly not claimed as executed PASS evidence. A full repository checkout was also unavailable in that runtime; existing application files were not modified by TASK-004.

## Important boundary

This runtime is an application contract, not an operating-system sandbox. Deliberately malicious future code could still create unrelated network sockets. Repository policy/tool sandboxing will address broader process/network permissions in later tasks.

`OpenAICompatibleLocalRuntime` is intentionally compatible with local servers that expose the common `/v1/models` and `/v1/chat/completions` API shape. TASK-004 does not select or install a specific inference engine or model.

## Result

No unresolved correctness, security, or architectural blocker remains within the scope of TASK-004. The local runtime is ready to serve as the execution boundary for the future local prompt/context compiler.
