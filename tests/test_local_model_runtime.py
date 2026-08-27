import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest

from local_dev.local_models import (
    LocalGenerationRequest,
    LocalMessage,
    LocalMessageRole,
    LocalRuntimeProtocolError,
    LocalRuntimeStatus,
    LocalRuntimeTimeout,
    OpenAICompatibleLocalRuntime,
    UrllibLocalHttpTransport,
)
from local_dev.local_models.openai_compatible import _HttpResponse


class FakeTransport:
    def __init__(self, response: _HttpResponse | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> _HttpResponse:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def request() -> LocalGenerationRequest:
    return LocalGenerationRequest(
        task_id=uuid4(),
        model="qwen-local",
        messages=(
            LocalMessage(LocalMessageRole.SYSTEM, "Be precise."),
            LocalMessage(LocalMessageRole.USER, "Review this code."),
        ),
        max_output_tokens=512,
        temperature=0,
        seed=42,
        metadata={"repo": "local_dev"},
    )


def runtime(transport: FakeTransport) -> OpenAICompatibleLocalRuntime:
    return OpenAICompatibleLocalRuntime(
        "http://127.0.0.1:11434/v1",
        transport=transport,
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:11434/v1",
        "http://localhost:11434/v1",
        "http://192.168.1.10:11434/v1",
        "http://8.8.8.8:11434/v1",
        "http://user:pass@127.0.0.1:11434/v1",
        "http://127.0.0.1:11434/v1?x=1",
        "http://127.0.0.1:11434/v1#frag",
    ],
)
def test_runtime_rejects_non_strict_loopback_endpoints(url: str) -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleLocalRuntime(url)


def test_ipv6_loopback_is_allowed() -> None:
    instance = OpenAICompatibleLocalRuntime("http://[::1]:11434/v1")
    assert instance.name == "openai-compatible-local"


def test_health_parses_models() -> None:
    transport = FakeTransport(
        _HttpResponse(
            200,
            json.dumps({"data": [{"id": "qwen"}, {"id": "coder"}]}).encode(),
        )
    )
    result = runtime(transport).health()
    assert result.status is LocalRuntimeStatus.READY
    assert result.models == ("qwen", "coder")
    assert transport.calls[0]["url"] == "http://127.0.0.1:11434/v1/models"


def test_health_without_models_is_degraded() -> None:
    transport = FakeTransport(_HttpResponse(200, b'{"data":[]}'))
    result = runtime(transport).health()
    assert result.status is LocalRuntimeStatus.DEGRADED
    assert result.models == ()


def test_health_timeout_is_reported_as_unavailable() -> None:
    transport = FakeTransport(LocalRuntimeTimeout("timed out"))
    result = runtime(transport).health()
    assert result.status is LocalRuntimeStatus.UNAVAILABLE
    assert result.models == ()


def test_health_malformed_response_is_degraded() -> None:
    transport = FakeTransport(_HttpResponse(200, b'{"unexpected":true}'))
    result = runtime(transport).health()
    assert result.status is LocalRuntimeStatus.DEGRADED


def test_generate_sends_bounded_non_streaming_chat_request() -> None:
    transport = FakeTransport(
        _HttpResponse(
            200,
            json.dumps(
                {
                    "model": "qwen-local-resolved",
                    "choices": [
                        {
                            "message": {"content": "Reviewed."},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 3,
                        "total_tokens": 13,
                    },
                }
            ).encode(),
        )
    )
    result = runtime(transport).generate(request())

    call = transport.calls[0]
    payload = json.loads(call["body"].decode())  # type: ignore[union-attr]
    assert call["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert payload["model"] == "qwen-local"
    assert payload["max_tokens"] == 512
    assert payload["temperature"] == 0.0
    assert payload["seed"] == 42
    assert payload["stream"] is False
    assert "metadata" not in payload
    assert result.requested_model == "qwen-local"
    assert result.served_model == "qwen-local-resolved"
    assert result.output_text == "Reviewed."
    assert result.usage.total_tokens == 13


def test_generate_rejects_missing_text_content() -> None:
    transport = FakeTransport(
        _HttpResponse(
            200,
            json.dumps(
                {
                    "choices": [
                        {"message": {"content": None}, "finish_reason": "stop"}
                    ]
                }
            ).encode(),
        )
    )
    with pytest.raises(LocalRuntimeProtocolError, match="textual content"):
        runtime(transport).generate(request())


def test_generate_rejects_inconsistent_usage() -> None:
    transport = FakeTransport(
        _HttpResponse(
            200,
            json.dumps(
                {
                    "choices": [
                        {"message": {"content": "x"}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 12,
                    },
                }
            ).encode(),
        )
    )
    with pytest.raises(LocalRuntimeProtocolError, match="token totals"):
        runtime(transport).generate(request())


def test_invalid_http_status_is_protocol_failure() -> None:
    transport = FakeTransport(_HttpResponse(500, b"{}"))
    with pytest.raises(LocalRuntimeProtocolError, match="HTTP status 500"):
        runtime(transport).generate(request())


def test_contract_metadata_is_defensively_frozen() -> None:
    metadata = {"repo": "local_dev"}
    req = LocalGenerationRequest(
        task_id=uuid4(),
        model="qwen",
        messages=(LocalMessage(LocalMessageRole.USER, "hi"),),
        max_output_tokens=10,
        metadata=metadata,
    )
    metadata["repo"] = "mutated"
    assert req.metadata["repo"] == "local_dev"
    with pytest.raises(TypeError):
        req.metadata["new"] = "x"  # type: ignore[index]


def test_contract_rejects_invalid_sampling_values() -> None:
    with pytest.raises(ValueError, match="temperature"):
        LocalGenerationRequest(
            task_id=uuid4(),
            model="qwen",
            messages=(LocalMessage(LocalMessageRole.USER, "hi"),),
            max_output_tokens=10,
            temperature=2.1,
        )


class _RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(302)
        self.send_header("Location", "http://8.8.8.8/")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


class _LargeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        payload = b"x" * 128
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def _serve(handler: type[BaseHTTPRequestHandler]):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_default_transport_refuses_redirects() -> None:
    server, thread = _serve(_RedirectHandler)
    try:
        port = server.server_address[1]
        transport = UrllibLocalHttpTransport()
        with pytest.raises(LocalRuntimeProtocolError, match="HTTP status 302"):
            transport.request(
                method="GET",
                url=f"http://127.0.0.1:{port}/x",
                headers={},
                body=None,
                timeout_seconds=1,
                max_response_bytes=1024,
            )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_default_transport_enforces_response_size_limit() -> None:
    server, thread = _serve(_LargeHandler)
    try:
        port = server.server_address[1]
        transport = UrllibLocalHttpTransport()
        with pytest.raises(LocalRuntimeProtocolError, match="byte limit"):
            transport.request(
                method="GET",
                url=f"http://127.0.0.1:{port}/x",
                headers={},
                body=None,
                timeout_seconds=1,
                max_response_bytes=32,
            )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
