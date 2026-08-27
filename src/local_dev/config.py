from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_path: Path
    monthly_budget_eur: Decimal
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = _absolute_path(
            os.getenv("LOCAL_DEV_DATA_DIR", "~/.local/share/local-dev")
        )
        database_path = _absolute_path(
            os.getenv("LOCAL_DEV_DATABASE_PATH", str(data_dir / "local_dev.db"))
        )
        monthly_budget_eur = _positive_decimal(
            "LOCAL_DEV_MONTHLY_BUDGET_EUR",
            os.getenv("LOCAL_DEV_MONTHLY_BUDGET_EUR", "20"),
        )
        log_level = os.getenv("LOCAL_DEV_LOG_LEVEL", "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"invalid LOCAL_DEV_LOG_LEVEL: {log_level}")
        return cls(
            data_dir=data_dir,
            database_path=database_path,
            monthly_budget_eur=monthly_budget_eur,
            log_level=log_level,
        )


def _absolute_path(raw: str) -> Path:
    return Path(raw).expanduser().resolve(strict=False)


def _positive_decimal(name: str, raw: str) -> Decimal:
    try:
        value = Decimal(raw.strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal number") from exc
    if not value.is_finite():
        raise ValueError(f"{name} must be a finite decimal number")
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value
