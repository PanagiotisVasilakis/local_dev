from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol
from uuid import UUID


class LocalRuntimeError(RuntimeError):
    """Base class for local model runtime failures."""


class LocalRuntimeUnavailable(LocalRuntimeError):
    """The configured local runtime could not be reached."""


class LocalRuntimeTimeout(LocalRuntimeUnavailable):
    """The local runtime exceeded the configured request timeout."""


class LocalRuntimeProtocolError(LocalRuntimeError):
    """The local runtime returned an invalid or unsupported protocol response."""


class LocalMessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class LocalRuntimeStatus(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class LocalMessage:
    role: LocalMessageRole
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, LocalMessageRole):
            raise TypeError("role must be LocalMessageRole")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("message content must not be empty")


@dataclass(frozen=True, slots=True)
class LocalGenerationRequest:
    task_id: UUID
    model: str
    messages: tuple[LocalMessage, ...]
    max_output_tokens: int
    temperature: float = 0.0
    seed: int | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, UUID):
            raise TypeError("task_id must be UUID")
        if not isinstance(self.model, str):
            raise TypeError("model must be a string")
        model = self.model.strip()
        if not model:
            raise ValueError("model must not be empty")
        if isinstance(self.messages, (str, bytes)):
            raise TypeError("messages must be an iterable of LocalMessage values")
        try:
            messages = tuple(self.messages)
        except TypeError as exc:
            raise TypeError("messages must be an iterable of LocalMessage values") from exc
        if not messages:
            raise ValueError("at least one message is required")
        if not all(isinstance(message, LocalMessage) for message in messages):
            raise TypeError("messages must contain LocalMessage values")
        if (
            not isinstance(self.max_output_tokens, int)
            or isinstance(self.max_output_tokens, bool)
            or self.max_output_tokens <= 0
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        if not isinstance(self.temperature, (int, float)) or isinstance(self.temperature, bool):
            raise TypeError("temperature must be numeric")
        temperature = float(self.temperature)
        if not math.isfinite(temperature) or not 0.0 <= temperature <= 2.0:
            raise ValueError("temperature must be a finite value between 0.0 and 2.0")
        if self.seed is not None and (
            not isinstance(self.seed, int) or isinstance(self.seed, bool)
        ):
            raise TypeError("seed must be an integer when present")
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "temperature", temperature)
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class LocalUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer when present")
        if (
            self.prompt_tokens is not None
            and self.completion_tokens is not None
            and self.total_tokens is not None
            and self.total_tokens < self.prompt_tokens + self.completion_tokens
        ):
            raise ValueError("total_tokens cannot be smaller than prompt+completion tokens")


@dataclass(frozen=True, slots=True)
class LocalGenerationResponse:
    runtime_name: str
    requested_model: str
    served_model: str
    output_text: str
    finish_reason: str | None
    usage: LocalUsage

    def __post_init__(self) -> None:
        names = (self.runtime_name, self.requested_model, self.served_model)
        if not all(isinstance(value, str) for value in names):
            raise TypeError("runtime and model names must be strings")
        runtime_name = self.runtime_name.strip()
        requested_model = self.requested_model.strip()
        served_model = self.served_model.strip()
        if not runtime_name or not requested_model or not served_model:
            raise ValueError("runtime and model names must not be empty")
        if not isinstance(self.output_text, str) or not self.output_text.strip():
            raise ValueError("output_text must not be empty")
        if not isinstance(self.usage, LocalUsage):
            raise TypeError("usage must be LocalUsage")
        if self.finish_reason is not None:
            if not isinstance(self.finish_reason, str):
                raise TypeError("finish_reason must be a string when present")
            finish_reason = self.finish_reason.strip()
            if not finish_reason:
                raise ValueError("finish_reason must be non-empty when present")
            object.__setattr__(self, "finish_reason", finish_reason)
        object.__setattr__(self, "runtime_name", runtime_name)
        object.__setattr__(self, "requested_model", requested_model)
        object.__setattr__(self, "served_model", served_model)


@dataclass(frozen=True, slots=True)
class LocalRuntimeHealth:
    runtime_name: str
    status: LocalRuntimeStatus
    models: tuple[str, ...] = ()
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_name, str):
            raise TypeError("runtime_name must be a string")
        runtime_name = self.runtime_name.strip()
        if not runtime_name:
            raise ValueError("runtime_name must not be empty")
        if not isinstance(self.status, LocalRuntimeStatus):
            raise TypeError("status must be LocalRuntimeStatus")
        if isinstance(self.models, (str, bytes)):
            raise TypeError("models must be an iterable of model identifiers")
        try:
            raw_models = tuple(self.models)
        except TypeError as exc:
            raise TypeError("models must be an iterable of model identifiers") from exc
        if not all(isinstance(model, str) for model in raw_models):
            raise TypeError("health model identifiers must be strings")
        normalized_models = tuple(model.strip() for model in raw_models)
        if any(not model for model in normalized_models):
            raise ValueError("health model identifiers must not be empty")
        if len(set(normalized_models)) != len(normalized_models):
            raise ValueError("health model identifiers must be unique")
        if self.status is LocalRuntimeStatus.READY and not normalized_models:
            raise ValueError("READY health requires at least one advertised model")
        if self.status is LocalRuntimeStatus.UNAVAILABLE and normalized_models:
            raise ValueError("UNAVAILABLE health cannot advertise models")
        if self.detail is not None:
            if not isinstance(self.detail, str):
                raise TypeError("detail must be a string when present")
            if not self.detail.strip():
                raise ValueError("detail must be non-empty when present")
        object.__setattr__(self, "runtime_name", runtime_name)
        object.__setattr__(self, "models", normalized_models)


class LocalModelRuntime(Protocol):
    """Vendor-neutral boundary for non-billable local model inference."""

    @property
    def name(self) -> str: ...

    def health(self) -> LocalRuntimeHealth: ...

    def generate(self, request: LocalGenerationRequest) -> LocalGenerationResponse: ...


def _freeze_metadata(metadata: Mapping[str, str]) -> Mapping[str, str]:
    if not isinstance(metadata, Mapping):
        raise TypeError("metadata must be a mapping")
    copied: dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("metadata keys must be non-empty strings")
        if not isinstance(value, str):
            raise TypeError("metadata values must be strings")
        copied[key] = value
    return MappingProxyType(copied)
