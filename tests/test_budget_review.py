import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from local_dev.budget import BudgetConflict, BudgetGovernor, BudgetInvariantError
from local_dev.db import Database

NOW = datetime(2026, 8, 27, 20, 0, tzinfo=UTC)


def _reserve(governor: BudgetGovernor, *, key: str, task_id):
    return governor.reserve(
        idempotency_key=key,
        task_id=task_id,
        provider="p",
        model="m",
        worst_case_eur=Decimal("1"),
        now=NOW,
    )


def test_idempotent_lookup_still_enforces_durable_period_limit(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()
    task_id = uuid4()
    first = BudgetGovernor(database, Decimal("20"))
    _reserve(first, key="same", task_id=task_id)

    mismatched = BudgetGovernor(Database(tmp_path / "state.db"), Decimal("25"))
    with pytest.raises(BudgetInvariantError, match="durable period policy"):
        _reserve(mismatched, key="same", task_id=task_id)


def test_terminal_reservation_row_cannot_be_rewritten(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()
    governor = BudgetGovernor(database, Decimal("20"))
    reservation = _reserve(governor, key="settled", task_id=uuid4())
    governor.settle(reservation.reservation_id, actual_eur=Decimal("0.5"), now=NOW)

    with pytest.raises(sqlite3.IntegrityError, match="terminal budget reservations are immutable"):
        with database.connect() as connection:
            connection.execute(
                """
                UPDATE budget_reservations
                SET status = 'active', actual_micros = NULL, settled_at = NULL
                WHERE reservation_id = ?
                """,
                (str(reservation.reservation_id),),
            )


def test_settled_idempotency_key_cannot_authorize_another_call(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()
    governor = BudgetGovernor(database, Decimal("20"))
    task_id = uuid4()
    reservation = _reserve(governor, key="same-settled", task_id=task_id)
    governor.settle(reservation.reservation_id, actual_eur=Decimal("0.5"), now=NOW)

    with pytest.raises(BudgetConflict, match="terminal reservation"):
        _reserve(governor, key="same-settled", task_id=task_id)


def test_cancelled_idempotency_key_cannot_authorize_another_call(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()
    governor = BudgetGovernor(database, Decimal("20"))
    task_id = uuid4()
    reservation = _reserve(governor, key="same-cancelled", task_id=task_id)
    governor.cancel(reservation.reservation_id, now=NOW)

    with pytest.raises(BudgetConflict, match="terminal reservation"):
        _reserve(governor, key="same-cancelled", task_id=task_id)
