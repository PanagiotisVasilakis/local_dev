from __future__ import annotations

import ipaddress
import json
import math
import socket
from dataclasses import dataclass
from http.client import HTTPException
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from local_dev.local_models.contracts import (
    LocalGenerationRequest,
    LocalGenerationResponse,
    LocalRuntimeHealth,
    LocalRuntimeProtocolError,
    LocalRuntimeStatus,
    LocalRuntimeTimeout,
    LocalRuntimeUnavailable,
    LocalUsage,
)

_DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _HttpResponse:
    status: int
    body: bytes


class _HttpTransport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> _HttpResponse: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        # A redirect could escape the loopback-only boundary. Never follow one.
        return None


class UrllibLocalHttpTransport:
    """stdlib HTTP transport that refuses redirects and bounds response size."""

    def __init__(self) -> None:
        self._opener = build_opener(ProxyHandler({}), _NoRedirectHandler())

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> _HttpResponse:
        request = Request(url=url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                payload = response.read(max_response_bytes + 1)
                if len(payload) > max_response_bytes:
                    raise LocalRuntimeProtocolError(
                        "local runtime response exceeded the configured byte limit"
                    )
                return _HttpResponse(status=int(response.status), body=payload)
        except HTTPError as exc:
            # Redirects arrive here because they are intentionally not followed.
            raise LocalRuntimeProtocolError(
                f"local runtime returned HTTP status {exc.code}"
            ) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise LocalRuntimeTimeout("local runtime request timed out") from exc
        except URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise LocalRuntimeTimeout("local runtime request timed out") from exc
            raise LocalRuntimeUnavailable("local runtime could not be reached") from exc
        except HTTPException as exc:
            raise LocalRuntimeProtocolError("local runtime returned malformed HTTP") from exc
        except OSError as exc:
            raise LocalRuntimeUnavailable("local runtime transport failed") from exc


class OpenAICompatibleLocalRuntime:
    """OpenAI-compatible chat runtime restricted to a numeric loopback endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 120.0,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        api_key: str | None = None,
        runtime_name: str = "openai-compatible-local",
        transport: _HttpTransport | None = None,
    ) -> None:
        self._base_url = _validate_loopback_base_url(base_url)
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(float(timeout_seconds))
            or float(timeout_seconds) <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        if (
            not isinstance(max_response_bytes, int)
            or isinstance(max_response_bytes, bool)
            or max_response_bytes <= 0
        ):
            raise ValueError("max_response_bytes must be a positive integer")
        name = runtime_name.strip()
        if not name:
            raise ValueError("runtime_name must not be empty")
        if api_key is not None and (not isinstance(api_key, str) or not api_key.strip()):
            raise ValueError("api_key must be a non-empty string when present")

        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = max_response_bytes
        self._api_key = api_key
        self._name = name
        self._transport = transport or UrllibLocalHttpTransport()

    @property
    def name(self) -> str:
        return self._name

    def health(self) -> LocalRuntimeHealth:
        try:
            payload = self._request_json("GET", "models")
            models = _parse_models(payload)
            if not models:
                return LocalRuntimeHealth(
                    runtime_name=self.name,
                    status=LocalRuntimeStatus.DEGRADED,
                    detail="local runtime is reachable but advertises no models",
                )
            return LocalRuntimeHealth(
                runtime_name=self.name,
                status=LocalRuntimeStatus.READY,
                models=models,
            )
        except (LocalRuntimeTimeout, LocalRuntimeUnavailable) as exc:
            return LocalRuntimeHealth(
                runtime_name=self.name,
                status=LocalRuntimeStatus.UNAVAILABLE,
                detail=str(exc),
            )
        except LocalRuntimeProtocolError as exc:
            return LocalRuntimeHealth(
                runtime_name=self.name,
                status=LocalRuntimeStatus.DEGRADED,
                detail=str(exc),
            )

    def generate(self, request: LocalGenerationRequest) -> LocalGenerationResponse:
        if not isinstance(request, LocalGenerationRequest):
            raise TypeError("request must be LocalGenerationRequest")
        body: dict[str, object] = {
            "model": request.model,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "stream": False,
        }
        if request.seed is not None:
            body["seed"] = request.seed

        payload = self._request_json("POST", "chat/completions", body)
        return _parse_generation_response(self.name, request, payload)

    def _request_json(
        self,
        method: str,
        relative_path: str,
        payload: dict[str, object] | None = None,
    ) -> object:
        headers = {"Accept": "application/json"}
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"

        response = self._transport.request(
            method=method,
            url=f"{self._base_url}/{relative_path.lstrip('/')}",
            headers=headers,
            body=body,
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )
        if response.status < 200 or response.status >= 300:
            raise LocalRuntimeProtocolError(
                f"local runtime returned HTTP status {response.status}"
            )
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalRuntimeProtocolError(
                "local runtime returned invalid UTF-8 JSON"
            ) from exc


def _validate_loopback_base_url(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("base_url must not be empty")
    value = raw.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme != "http":
        raise ValueError("local runtime base_url must use http")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("local runtime base_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("local runtime base_url must not contain query or fragment")
    if parsed.hostname is None:
        raise ValueError("local runtime base_url must contain a host")
    try:
        host = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError(
            "local runtime host must be a numeric loopback address; DNS names are not allowed"
        ) from exc
    if not host.is_loopback:
        raise ValueError("local runtime host must be a loopback address")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("local runtime base_url contains an invalid port") from exc
    return value


def _parse_models(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        raise LocalRuntimeProtocolError("models response must be a JSON object")
    data = payload.get("data")
    if not isinstance(data, list):
        raise LocalRuntimeProtocolError("models response is missing a data array")
    models: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            raise LocalRuntimeProtocolError("models response contains a non-object item")
        model = item.get("id")
        if not isinstance(model, str) or not model.strip():
            raise LocalRuntimeProtocolError("models response contains an invalid model id")
        normalized = model.strip()
        if normalized in models:
            raise LocalRuntimeProtocolError("models response contains a duplicate model id")
        models.append(normalized)
    return tuple(models)


def _parse_generation_response(
    runtime_name: str,
    request: LocalGenerationRequest,
    payload: object,
) -> LocalGenerationResponse:
    if not isinstance(payload, dict):
        raise LocalRuntimeProtocolError("generation response must be a JSON object")

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LocalRuntimeProtocolError("generation response is missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise LocalRuntimeProtocolError("generation choice must be an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise LocalRuntimeProtocolError("generation choice is missing message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LocalRuntimeProtocolError("generation response contains no textual content")

    served_model = payload.get("model", request.model)
    if not isinstance(served_model, str) or not served_model.strip():
        raise LocalRuntimeProtocolError("generation response contains an invalid model")

    finish_reason = first.get("finish_reason")
    if finish_reason is not None and (
        not isinstance(finish_reason, str) or not finish_reason.strip()
    ):
        raise LocalRuntimeProtocolError("generation response contains an invalid finish reason")

    usage = _parse_usage(payload.get("usage"))
    return LocalGenerationResponse(
        runtime_name=runtime_name,
        requested_model=request.model,
        served_model=served_model.strip(),
        output_text=content,
        finish_reason=finish_reason,
        usage=usage,
    )


def _parse_usage(payload: object) -> LocalUsage:
    if payload is None:
        return LocalUsage()
    if not isinstance(payload, dict):
        raise LocalRuntimeProtocolError("usage must be an object when present")

    values: dict[str, int | None] = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = payload.get(name)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise LocalRuntimeProtocolError(f"usage.{name} must be a non-negative integer")
        values[name] = value
    try:
        return LocalUsage(**values)
    except ValueError as exc:
        raise LocalRuntimeProtocolError("usage token totals are inconsistent") from exc
