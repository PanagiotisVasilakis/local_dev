from pathlib import Path

import pytest

from local_dev.config import Settings


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LOCAL_DEV_DATA_DIR",
        "LOCAL_DEV_DATABASE_PATH",
        "LOCAL_DEV_MONTHLY_BUDGET_EUR",
        "LOCAL_DEV_LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.monthly_budget_eur == 20.0
    assert settings.log_level == "INFO"
    assert settings.database_path == settings.data_dir / "local_dev.db"


def test_settings_reject_non_positive_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_DEV_MONTHLY_BUDGET_EUR", "0")
    with pytest.raises(ValueError, match="greater than zero"):
        Settings.from_env()


def test_settings_accept_explicit_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    monkeypatch.setenv("LOCAL_DEV_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOCAL_DEV_DATABASE_PATH", str(db_path))

    settings = Settings.from_env()

    assert settings.data_dir == tmp_path
    assert settings.database_path == db_path
