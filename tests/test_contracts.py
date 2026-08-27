from pathlib import Path
from uuid import uuid4

import pytest

from local_dev.core.contracts import (
    AgentMode,
    EvidenceRef,
    ModelRequest,
    ResearchMode,
    TaskSpec,
    VerificationResult,
    VerificationStatus,
)


def test_task_spec_requires_absolute_repository_root(tmp_path: Path) -> None:
    task = TaskSpec(
        prompt="inspect architecture",
        repository_root=tmp_path.resolve(),
        mode=AgentMode.PLAN,
        research=ResearchMode.WEB,
    )
    assert task.mode is AgentMode.PLAN
    assert task.research is ResearchMode.WEB


def test_task_spec_rejects_relative_repository_root() -> None:
    with pytest.raises(ValueError, match="absolute"):
        TaskSpec(prompt="inspect", repository_root=Path("relative/repo"))


def test_task_spec_rejects_blank_prompt(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="prompt"):
        TaskSpec(prompt="   ", repository_root=tmp_path.resolve())


def test_task_spec_metadata_is_defensively_immutable(tmp_path: Path) -> None:
    metadata = {"origin": "ui"}
    task = TaskSpec(prompt="x", repository_root=tmp_path.resolve(), metadata=metadata)
    metadata["origin"] = "mutated"
    assert task.metadata["origin"] == "ui"
    with pytest.raises(TypeError):
        task.metadata["origin"] = "mutated"  # type: ignore[index]


def test_model_request_rejects_invalid_token_limit(tmp_path: Path) -> None:
    task = TaskSpec(prompt="x", repository_root=tmp_path.resolve())
    with pytest.raises(ValueError, match="positive"):
        ModelRequest(
            task_id=task.task_id,
            provider="provider",
            model="model",
            input_text="context",
            max_output_tokens=0,
        )


def test_passed_verification_requires_evidence() -> None:
    with pytest.raises(ValueError, match="requires evidence"):
        VerificationResult(status=VerificationStatus.PASSED, summary="passed")


def test_passed_verification_accepts_evidence() -> None:
    evidence = EvidenceRef(source="pytest", locator="run:1", content_hash="abc")
    result = VerificationResult(
        status=VerificationStatus.PASSED,
        summary="tests passed",
        evidence=(evidence,),
    )
    assert result.evidence == (evidence,)


def test_model_request_metadata_is_defensively_immutable() -> None:
    metadata = {"route": "default"}
    request = ModelRequest(
        task_id=uuid4(),
        provider="provider",
        model="model",
        input_text="context",
        max_output_tokens=1,
        metadata=metadata,
    )
    metadata["route"] = "mutated"
    assert request.metadata["route"] == "default"
