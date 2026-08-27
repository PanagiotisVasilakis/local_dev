from decimal import Decimal
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

    assert settings.monthly_budget_eur == Decimal("20")
    assert settings.log_level == "INFO"
    assert settings.data_dir.is_absolute()
    assert settings.database_path == settings.data_dir / "local_dev.db"


@pytest.mark.parametrize("raw", ["0", "-1", "nan", "inf", "-inf", "not-a-number", ""])
def test_settings_reject_invalid_budget(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("LOCAL_DEV_MONTHLY_BUDGET_EUR", raw)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_settings_preserves_exact_decimal_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_DEV_MONTHLY_BUDGET_EUR", "19.999999")
    settings = Settings.from_env()
    assert settings.monthly_budget_eur == Decimal("19.999999")


def test_settings_accept_explicit_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    monkeypatch.setenv("LOCAL_DEV_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOCAL_DEV_DATABASE_PATH", str(db_path))

    settings = Settings.from_env()

    assert settings.data_dir == tmp_path
    assert settings.database_path == db_path


def test_settings_normalizes_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_DEV_LOG_LEVEL", "debug")
    assert Settings.from_env().log_level == "DEBUG"


def test_settings_rejects_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_DEV_LOG_LEVEL", "verbose")
    with pytest.raises(ValueError, match="invalid LOCAL_DEV_LOG_LEVEL"):
        Settings.from_env()
