from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from uuid import UUID, uuid4


class AgentMode(StrEnum):
    ASK = "ask"
    PLAN = "plan"
    AGENT = "agent"


class ResearchMode(StrEnum):
    OFF = "off"
    WEB = "web"
    DEEP = "deep"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class VerificationStatus(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Reference to evidence produced by a deterministic source or tool."""

    source: str
    locator: str
    content_hash: str | None = None
    commit_sha: str | None = None

    def __post_init__(self) -> None:
        source = _non_empty_string(self.source, "evidence source")
        locator = _non_empty_string(self.locator, "evidence locator")
        content_hash = _optional_non_empty_string(self.content_hash, "content_hash")
        commit_sha = _optional_non_empty_string(self.commit_sha, "commit_sha")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "locator", locator)
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "commit_sha", commit_sha)


@dataclass(frozen=True, slots=True)
class TaskSpec:
    prompt: str
    repository_root: Path
    mode: AgentMode = AgentMode.ASK
    research: ResearchMode = ResearchMode.OFF
    risk: RiskLevel = RiskLevel.MEDIUM
    task_id: UUID = field(default_factory=uuid4)
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _non_empty_string(self.prompt, "prompt", strip_result=False)
        if not isinstance(self.repository_root, Path):
            raise TypeError("repository_root must be pathlib.Path")
        if not self.repository_root.is_absolute():
            raise ValueError("repository_root must be an absolute path")
        if not isinstance(self.mode, AgentMode):
            raise TypeError("mode must be AgentMode")
        if not isinstance(self.research, ResearchMode):
            raise TypeError("research must be ResearchMode")
        if not isinstance(self.risk, RiskLevel):
            raise TypeError("risk must be RiskLevel")
        _require_uuid(self.task_id, "task_id")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class ModelRequest:
    task_id: UUID
    provider: str
    model: str
    input_text: str
    max_output_tokens: int
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_uuid(self.task_id, "task_id")
        provider = _non_empty_string(self.provider, "provider")
        model = _non_empty_string(self.model, "model")
        _non_empty_string(self.input_text, "input_text", strip_result=False)
        if (
            not isinstance(self.max_output_tokens, int)
            or isinstance(self.max_output_tokens, bool)
            or self.max_output_tokens <= 0
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: VerificationStatus
    summary: str
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, VerificationStatus):
            raise TypeError("status must be VerificationStatus")
        _non_empty_string(self.summary, "verification summary", strip_result=False)
        try:
            evidence = tuple(self.evidence)
        except TypeError as exc:
            raise TypeError("evidence must be an iterable of EvidenceRef values") from exc
        if not all(isinstance(item, EvidenceRef) for item in evidence):
            raise TypeError("evidence must contain EvidenceRef values")
        if self.status is VerificationStatus.PASSED and not evidence:
            raise ValueError("passed verification requires evidence")
        object.__setattr__(self, "evidence", evidence)


def _require_uuid(value: object, name: str) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"{name} must be UUID")
    return value


def _non_empty_string(
    value: object,
    name: str,
    *,
    strip_result: bool = True,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized if strip_result else value


def _optional_non_empty_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _non_empty_string(value, name)


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
