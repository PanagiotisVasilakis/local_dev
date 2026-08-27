from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Mapping
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
        if not self.source.strip():
            raise ValueError("evidence source must not be empty")
        if not self.locator.strip():
            raise ValueError("evidence locator must not be empty")


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
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        if not self.repository_root.is_absolute():
            raise ValueError("repository_root must be an absolute path")


@dataclass(frozen=True, slots=True)
class ModelRequest:
    task_id: UUID
    provider: str
    model: str
    input_text: str
    max_output_tokens: int
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("provider and model must not be empty")
        if not self.input_text.strip():
            raise ValueError("input_text must not be empty")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: VerificationStatus
    summary: str
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("verification summary must not be empty")
