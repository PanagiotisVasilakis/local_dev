from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_CEILING
from enum import StrEnum
from uuid import UUID, uuid4

from local_dev.db import Database

_MICROS_PER_EUR = Decimal("1000000")
_SQLITE_MAX_INT = 2**63 - 1


class BudgetError(RuntimeError):
    """Base class for deterministic budget-governor failures."""


class BudgetExceeded(BudgetError):
    """The requested reservation would exceed the configured monthly ceiling."""


class BudgetConflict(BudgetError):
    """An idempotency or lifecycle request conflicts with durable ledger state."""


class BudgetInvariantError(BudgetError):
    """A fail-closed accounting invariant was violated."""


class ReservationStatus(StrEnum):
    ACTIVE = "active"
    SETTLED = "settled"
    CANCELLED = "cancelled"
    BREACHED = "breached"


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    reservation_id: UUID
    idempotency_key: str
    period_utc: str
    task_id: UUID
    provider: str
    model: str
    reserved_eur: Decimal
    status: ReservationStatus


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    period_utc: str
    limit_eur: Decimal
    spent_eur: Decimal
    reserved_eur: Decimal

    @property
    def available_eur(self) -> Decimal:
        return self.limit_eur - self.spent_eur - self.reserved_eur


class BudgetGovernor:
    """SQLite-backed reservation ledger that authorizes paid calls fail-closed."""

    def __init__(self, database: Database, monthly_limit_eur: Decimal) -> None:
        self._database = database
        self._limit_micros = _eur_to_micros(monthly_limit_eur)

    def reserve(
        self,
        *,
        idempotency_key: str,
        task_id: UUID,
        provider: str,
        model: str,
        worst_case_eur: Decimal,
        now: datetime | None = None,
    ) -> BudgetReservation:
        key = idempotency_key.strip()
        provider = provider.strip()
        model = model.strip()
        if not key or not provider or not model:
            raise ValueError("idempotency_key, provider, and model must not be empty")
        reserved_micros = _eur_to_micros(worst_case_eur)
        period = _period(now)
        created_at = _timestamp(now)

        with self._database.immediate_transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM budget_reservations WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if existing is not None:
                reservation = _row_to_reservation(existing)
                if (
                    reservation.period_utc != period
                    or reservation.task_id != task_id
                    or reservation.provider != provider
                    or reservation.model != model
                    or _eur_to_micros(reservation.reserved_eur) != reserved_micros
                ):
                    raise BudgetConflict("idempotency key was already used with different parameters")
                self._ensure_period_policy(connection, period, created_at)
                if reservation.status is not ReservationStatus.ACTIVE:
                    raise BudgetConflict(
                        "idempotency key refers to a terminal reservation and cannot authorize "
                        "another paid call"
                    )
                return reservation

            self._ensure_period_policy(connection, period, created_at)
            breach = connection.execute(
                "SELECT reservation_id FROM budget_reservations "
                "WHERE period_utc = ? AND status = 'breached' LIMIT 1",
                (period,),
            ).fetchone()
            if breach is not None:
                raise BudgetInvariantError("budget period is locked after an accounting breach")

            spent, active = _totals(connection, period)
            if spent + active + reserved_micros > self._limit_micros:
                raise BudgetExceeded(
                    "paid API call blocked: monthly budget would be exceeded; "
                    "no paid call was authorized"
                )

            reservation_id = uuid4()
            connection.execute(
                """
                INSERT INTO budget_reservations(
                    reservation_id, idempotency_key, period_utc, task_id, provider, model,
                    reserved_micros, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    str(reservation_id),
                    key,
                    period,
                    str(task_id),
                    provider,
                    model,
                    reserved_micros,
                    created_at,
                ),
            )
            return BudgetReservation(
                reservation_id=reservation_id,
                idempotency_key=key,
                period_utc=period,
                task_id=task_id,
                provider=provider,
                model=model,
                reserved_eur=_micros_to_eur(reserved_micros),
                status=ReservationStatus.ACTIVE,
            )

    def settle(
        self,
        reservation_id: UUID,
        *,
        actual_eur: Decimal,
        now: datetime | None = None,
    ) -> BudgetReservation:
        actual_micros = _eur_to_micros(actual_eur, allow_zero=True)
        timestamp = _timestamp(now)
        breached = False

        with self._database.immediate_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM budget_reservations WHERE reservation_id = ?",
                (str(reservation_id),),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown reservation: {reservation_id}")
            status = ReservationStatus(row["status"])
            if status is ReservationStatus.SETTLED:
                if int(row["actual_micros"]) != actual_micros:
                    raise BudgetConflict("reservation already settled with a different actual cost")
                return _row_to_reservation(row)
            if status is ReservationStatus.BREACHED:
                if int(row["actual_micros"]) != actual_micros:
                    raise BudgetConflict("breached reservation already recorded a different actual cost")
                raise BudgetInvariantError("reservation is already in breached accounting state")
            if status is not ReservationStatus.ACTIVE:
                raise BudgetConflict(f"cannot settle reservation in status {status}")

            reserved_micros = int(row["reserved_micros"])
            if actual_micros > reserved_micros:
                connection.execute(
                    """
                    UPDATE budget_reservations
                    SET status = 'breached', actual_micros = ?, settled_at = ?,
                        breach_reason = 'actual cost exceeded reserved worst-case amount'
                    WHERE reservation_id = ?
                    """,
                    (actual_micros, timestamp, str(reservation_id)),
                )
                breached = True
            else:
                connection.execute(
                    """
                    UPDATE budget_reservations
                    SET status = 'settled', actual_micros = ?, settled_at = ?
                    WHERE reservation_id = ?
                    """,
                    (actual_micros, timestamp, str(reservation_id)),
                )

        if breached:
            raise BudgetInvariantError(
                "actual provider cost exceeded the pre-authorized worst-case reservation"
            )
        return BudgetReservation(
            reservation_id=UUID(str(row["reservation_id"])),
            idempotency_key=str(row["idempotency_key"]),
            period_utc=str(row["period_utc"]),
            task_id=UUID(str(row["task_id"])),
            provider=str(row["provider"]),
            model=str(row["model"]),
            reserved_eur=_micros_to_eur(int(row["reserved_micros"])),
            status=ReservationStatus.SETTLED,
        )

    def cancel(self, reservation_id: UUID, *, now: datetime | None = None) -> None:
        timestamp = _timestamp(now)
        with self._database.immediate_transaction() as connection:
            row = connection.execute(
                "SELECT status FROM budget_reservations WHERE reservation_id = ?",
                (str(reservation_id),),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown reservation: {reservation_id}")
            status = ReservationStatus(row["status"])
            if status is ReservationStatus.CANCELLED:
                return
            if status is not ReservationStatus.ACTIVE:
                raise BudgetConflict(f"cannot cancel reservation in status {status}")
            connection.execute(
                "UPDATE budget_reservations SET status='cancelled', cancelled_at=? "
                "WHERE reservation_id=?",
                (timestamp, str(reservation_id)),
            )

    def snapshot(self, *, now: datetime | None = None) -> BudgetSnapshot:
        period = _period(now)
        with self._database.connect() as connection:
            policy = connection.execute(
                "SELECT limit_micros FROM budget_periods WHERE period_utc = ?",
                (period,),
            ).fetchone()
            if policy is not None and int(policy["limit_micros"]) != self._limit_micros:
                raise BudgetInvariantError(
                    "configured monthly budget differs from the durable period policy"
                )
            spent, active = _totals(connection, period)
        return BudgetSnapshot(
            period_utc=period,
            limit_eur=_micros_to_eur(self._limit_micros),
            spent_eur=_micros_to_eur(spent),
            reserved_eur=_micros_to_eur(active),
        )

    def _ensure_period_policy(
        self, connection: sqlite3.Connection, period: str, created_at: str
    ) -> None:
        row = connection.execute(
            "SELECT limit_micros FROM budget_periods WHERE period_utc = ?",
            (period,),
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO budget_periods(period_utc, limit_micros, created_at) VALUES (?, ?, ?)",
                (period, self._limit_micros, created_at),
            )
            return
        if int(row["limit_micros"]) != self._limit_micros:
            raise BudgetInvariantError(
                "configured monthly budget differs from the durable period policy"
            )


def _totals(connection: sqlite3.Connection, period: str) -> tuple[int, int]:
    row = connection.execute(
        """
        SELECT
            COALESCE(SUM(
                CASE WHEN status IN ('settled','breached') THEN actual_micros ELSE 0 END
            ), 0),
            COALESCE(SUM(CASE WHEN status = 'active' THEN reserved_micros ELSE 0 END), 0)
        FROM budget_reservations
        WHERE period_utc = ?
        """,
        (period,),
    ).fetchone()
    return int(row[0]), int(row[1])


def _row_to_reservation(row: sqlite3.Row) -> BudgetReservation:
    return BudgetReservation(
        reservation_id=UUID(str(row["reservation_id"])),
        idempotency_key=str(row["idempotency_key"]),
        period_utc=str(row["period_utc"]),
        task_id=UUID(str(row["task_id"])),
        provider=str(row["provider"]),
        model=str(row["model"]),
        reserved_eur=_micros_to_eur(int(row["reserved_micros"])),
        status=ReservationStatus(str(row["status"])),
    )


def _eur_to_micros(value: Decimal, *, allow_zero: bool = False) -> int:
    if not isinstance(value, Decimal):
        raise TypeError("monetary values must be Decimal")
    if not value.is_finite():
        raise ValueError("monetary values must be finite")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError("monetary value must be positive")
    micros = int((value * _MICROS_PER_EUR).to_integral_value(rounding=ROUND_CEILING))
    if micros > _SQLITE_MAX_INT:
        raise ValueError("monetary value exceeds durable ledger range")
    return micros


def _micros_to_eur(value: int) -> Decimal:
    return Decimal(value) / _MICROS_PER_EUR


def _utc(now: datetime | None) -> datetime:
    value = now or datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)


def _period(now: datetime | None) -> str:
    value = _utc(now)
    return f"{value.year:04d}-{value.month:02d}"


def _timestamp(now: datetime | None) -> str:
    return _utc(now).isoformat()
