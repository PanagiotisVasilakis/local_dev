from pathlib import Path

import pytest

from local_dev.core.contracts import AgentMode, ModelRequest, ResearchMode, TaskSpec


def test_task_spec_requires_absolute_repository_root(tmp_path: Path) -> None:
    task = TaskSpec(
        prompt="inspect architecture",
        repository_root=tmp_path.resolve(),
        mode=AgentMode.PLAN,
        research=ResearchMode.WEB,
    )
    assert task.mode is AgentMode.PLAN
    assert task.research is ResearchMode.WEB


def test_task_spec_rejects_blank_prompt(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="prompt"):
        TaskSpec(prompt="   ", repository_root=tmp_path.resolve())


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
