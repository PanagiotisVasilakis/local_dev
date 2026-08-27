import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from local_dev.budget import (
    BudgetConflict,
    BudgetExceeded,
    BudgetGovernor,
    BudgetInvariantError,
    ReservationStatus,
)
from local_dev.db import Database

NOW = datetime(2026, 8, 27, 20, 0, tzinfo=UTC)


def governor(tmp_path: Path, limit: str = "20") -> BudgetGovernor:
    database = Database(tmp_path / "state.db")
    database.migrate()
    return BudgetGovernor(database, Decimal(limit))


def reserve(g: BudgetGovernor, key: str, amount: str = "1"):
    return g.reserve(
        idempotency_key=key,
        task_id=uuid4(),
        provider="provider",
        model="model",
        worst_case_eur=Decimal(amount),
        now=NOW,
    )


def test_reservation_reduces_available_budget(tmp_path: Path) -> None:
    g = governor(tmp_path)
    reserve(g, "one", "3.25")
    snapshot = g.snapshot(now=NOW)
    assert snapshot.spent_eur == 0
    assert snapshot.reserved_eur == Decimal("3.25")
    assert snapshot.available_eur == Decimal("16.75")


def test_settlement_releases_unused_reservation(tmp_path: Path) -> None:
    g = governor(tmp_path)
    r = reserve(g, "one", "3")
    settled = g.settle(r.reservation_id, actual_eur=Decimal("1.2"), now=NOW)
    assert settled.status is ReservationStatus.SETTLED
    snapshot = g.snapshot(now=NOW)
    assert snapshot.spent_eur == Decimal("1.2")
    assert snapshot.reserved_eur == 0
    assert snapshot.available_eur == Decimal("18.8")


def test_cancel_releases_full_reservation(tmp_path: Path) -> None:
    g = governor(tmp_path)
    r = reserve(g, "one", "3")
    g.cancel(r.reservation_id, now=NOW)
    assert g.snapshot(now=NOW).available_eur == Decimal("20")


def test_reservation_that_would_exceed_limit_is_rejected(tmp_path: Path) -> None:
    g = governor(tmp_path, "2")
    reserve(g, "one", "1.5")
    with pytest.raises(BudgetExceeded):
        reserve(g, "two", "0.500001")


def test_exact_limit_is_allowed(tmp_path: Path) -> None:
    g = governor(tmp_path, "2")
    reserve(g, "one", "2")
    assert g.snapshot(now=NOW).available_eur == 0


def test_micro_euro_rounding_is_conservative(tmp_path: Path) -> None:
    g = governor(tmp_path, "0.000001")
    reserve(g, "one", "0.0000001")
    with pytest.raises(BudgetExceeded):
        reserve(g, "two", "0.0000001")


def test_duplicate_idempotency_key_returns_same_reservation(tmp_path: Path) -> None:
    g = governor(tmp_path)
    task_id = uuid4()
    args = dict(
        idempotency_key="same",
        task_id=task_id,
        provider="p",
        model="m",
        worst_case_eur=Decimal("1"),
        now=NOW,
    )
    first = g.reserve(**args)
    second = g.reserve(**args)
    assert first == second
    assert g.snapshot(now=NOW).reserved_eur == Decimal("1")


def test_duplicate_idempotency_key_with_different_request_fails(tmp_path: Path) -> None:
    g = governor(tmp_path)
    task_id = uuid4()
    g.reserve(
        idempotency_key="same",
        task_id=task_id,
        provider="p",
        model="m",
        worst_case_eur=Decimal("1"),
        now=NOW,
    )
    with pytest.raises(BudgetConflict):
        g.reserve(
            idempotency_key="same",
            task_id=task_id,
            provider="p",
            model="other",
            worst_case_eur=Decimal("1"),
            now=NOW,
        )


def test_settlement_is_idempotent_for_same_actual_cost(tmp_path: Path) -> None:
    g = governor(tmp_path)
    r = reserve(g, "one", "2")
    g.settle(r.reservation_id, actual_eur=Decimal("1"), now=NOW)
    again = g.settle(r.reservation_id, actual_eur=Decimal("1"), now=NOW)
    assert again.status is ReservationStatus.SETTLED
    assert g.snapshot(now=NOW).spent_eur == Decimal("1")


def test_settlement_conflict_for_different_actual_cost(tmp_path: Path) -> None:
    g = governor(tmp_path)
    r = reserve(g, "one", "2")
    g.settle(r.reservation_id, actual_eur=Decimal("1"), now=NOW)
    with pytest.raises(BudgetConflict):
        g.settle(r.reservation_id, actual_eur=Decimal("1.1"), now=NOW)


def test_actual_cost_above_reservation_records_breach_and_locks_period(tmp_path: Path) -> None:
    g = governor(tmp_path)
    r = reserve(g, "one", "1")
    with pytest.raises(BudgetInvariantError):
        g.settle(r.reservation_id, actual_eur=Decimal("1.01"), now=NOW)
    assert g.snapshot(now=NOW).spent_eur == Decimal("1.01")
    with pytest.raises(BudgetInvariantError):
        reserve(g, "two", "1")


def test_previous_month_does_not_consume_new_month_budget(tmp_path: Path) -> None:
    g = governor(tmp_path, "2")
    july = datetime(2026, 7, 31, 23, tzinfo=UTC)
    august = datetime(2026, 8, 1, 0, tzinfo=UTC)
    g.reserve(
        idempotency_key="july",
        task_id=uuid4(),
        provider="p",
        model="m",
        worst_case_eur=Decimal("2"),
        now=july,
    )
    assert g.snapshot(now=august).available_eur == Decimal("2")


def test_naive_datetime_is_rejected(tmp_path: Path) -> None:
    g = governor(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        g.snapshot(now=datetime(2026, 8, 1))


def test_non_decimal_money_is_rejected(tmp_path: Path) -> None:
    g = governor(tmp_path)
    with pytest.raises(TypeError, match="Decimal"):
        g.reserve(
            idempotency_key="bad",
            task_id=uuid4(),
            provider="p",
            model="m",
            worst_case_eur=1.0,  # type: ignore[arg-type]
            now=NOW,
        )


def test_concurrent_reservations_cannot_oversubscribe_limit(tmp_path: Path) -> None:
    g = governor(tmp_path, "1")

    def attempt(key: str) -> str:
        try:
            reserve(g, key, "0.75")
            return "ok"
        except BudgetExceeded:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, ["a", "b"]))
    assert sorted(outcomes) == ["blocked", "ok"]
    assert g.snapshot(now=NOW).reserved_eur == Decimal("0.75")


def test_period_policy_is_durable_and_limit_mismatch_fails_closed(tmp_path: Path) -> None:
    first = governor(tmp_path, "20")
    reserve(first, "one", "1")
    second = BudgetGovernor(Database(tmp_path / "state.db"), Decimal("25"))
    with pytest.raises(BudgetInvariantError, match="durable period policy"):
        second.snapshot(now=NOW)
    with pytest.raises(BudgetInvariantError, match="durable period policy"):
        second.reserve(
            idempotency_key="two",
            task_id=uuid4(),
            provider="p",
            model="m",
            worst_case_eur=Decimal("1"),
            now=NOW,
        )


def test_budget_ledger_identity_and_deletion_are_database_immutable(tmp_path: Path) -> None:
    g = governor(tmp_path)
    r = reserve(g, "one", "1")
    database = Database(tmp_path / "state.db")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connect() as connection:
            connection.execute(
                "UPDATE budget_reservations SET reserved_micros = 2 WHERE reservation_id = ?",
                (str(r.reservation_id),),
            )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connect() as connection:
            connection.execute(
                "DELETE FROM budget_reservations WHERE reservation_id = ?",
                (str(r.reservation_id),),
            )


def test_breached_settlement_retry_is_deterministic(tmp_path: Path) -> None:
    g = governor(tmp_path)
    r = reserve(g, "one", "1")
    with pytest.raises(BudgetInvariantError):
        g.settle(r.reservation_id, actual_eur=Decimal("1.01"), now=NOW)
    with pytest.raises(BudgetInvariantError, match="already in breached"):
        g.settle(r.reservation_id, actual_eur=Decimal("1.01"), now=NOW)
    with pytest.raises(BudgetConflict, match="different actual"):
        g.settle(r.reservation_id, actual_eur=Decimal("1.02"), now=NOW)


def test_monetary_value_must_fit_sqlite_integer_ledger(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()
    with pytest.raises(ValueError, match="durable ledger range"):
        BudgetGovernor(database, Decimal("1e30"))


def test_period_policy_cannot_be_mutated_directly(tmp_path: Path) -> None:
    g = governor(tmp_path)
    reserve(g, "one", "1")
    database = Database(tmp_path / "state.db")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.connect() as connection:
            connection.execute(
                "UPDATE budget_periods SET limit_micros = 999999999 "
                "WHERE period_utc = '2026-08'"
            )
