from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from local_dev.budget import BudgetGovernor, BudgetInvariantError, BudgetSnapshot
from local_dev.core.contracts import ModelRequest
from local_dev.db import Database
from local_dev.providers.contracts import (
    CostQuote,
    PaidProviderAdapter,
    ProviderNotSentError,
    ProviderResponse,
)


class ProviderBoundaryError(RuntimeError):
    """Base class for paid-provider boundary failures."""


class ProviderContractError(ProviderBoundaryError):
    """A provider adapter violated the boundary contract."""


class ProviderReplayBlocked(ProviderBoundaryError):
    """A durable call record proves retrying could duplicate a paid request."""


class ProviderDispatchUncertain(ProviderBoundaryError):
    """A dispatch may have reached the provider, so retry is blocked fail-closed."""

    def __init__(self, call_id: UUID, reservation_id: UUID) -> None:
        self.call_id = call_id
        self.reservation_id = reservation_id
        super().__init__(
            "provider dispatch outcome is uncertain; reservation remains held and "
            "this idempotency key must not be resent"
        )


class ProviderCallStatus(StrEnum):
    PREPARED = "prepared"
    DISPATCHING = "dispatching"
    COMPLETED = "completed"
    NOT_SENT = "not_sent"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class PaidCallResult:
    call_id: UUID
    reservation_id: UUID
    response: ProviderResponse


@dataclass(frozen=True, slots=True)
class _CallRecord:
    call_id: UUID
    reservation_id: UUID
    request_fingerprint: str
    status: ProviderCallStatus


class PaidCallGateway:
    """The application-owned boundary for every paid model-provider call."""

    def __init__(
        self,
        database: Database,
        monthly_limit_eur: Decimal,
        adapters: Iterable[PaidProviderAdapter],
    ) -> None:
        self._database = database
        self._budget = BudgetGovernor(database, monthly_limit_eur)
        self._adapters = _build_registry(adapters)

    def call(
        self,
        request: ModelRequest,
        *,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PaidCallResult:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key must not be empty")

        request = _normalized_request(request)
        provider = request.provider
        model = request.model
        adapter = self._adapters.get(provider)
        if adapter is None:
            raise ProviderContractError(f"no paid provider adapter registered for {provider!r}")

        quote = adapter.quote(request)
        self._validate_quote(request, quote)
        fingerprint = _request_fingerprint(request, quote)

        reservation = self._budget.reserve(
            idempotency_key=key,
            task_id=request.task_id,
            provider=provider,
            model=model,
            worst_case_eur=quote.worst_case_eur,
            now=now,
        )

        record = self._ensure_call_record(
            key=key,
            reservation_id=reservation.reservation_id,
            request_fingerprint=fingerprint,
            provider=provider,
            model=model,
            now=now,
        )
        self._begin_dispatch(record.call_id, now=now)

        try:
            response = adapter.execute(request, quote)
        except ProviderNotSentError as exc:
            self._finish_call(
                record.call_id,
                ProviderCallStatus.NOT_SENT,
                error_type=type(exc).__name__,
                now=now,
            )
            try:
                self._budget.cancel(reservation.reservation_id, now=now)
            except Exception as cancel_exc:
                raise ProviderBoundaryError(
                    "provider proved the call was not sent, but reservation cancellation "
                    "failed; budget remains conservatively held"
                ) from cancel_exc
            raise
        except Exception as exc:
            self._mark_uncertain(record.call_id, type(exc).__name__, now=now)
            raise ProviderDispatchUncertain(
                record.call_id, reservation.reservation_id
            ) from exc

        if not isinstance(response, ProviderResponse):
            self._mark_uncertain(record.call_id, "InvalidProviderResponse", now=now)
            raise ProviderContractError(
                "provider adapter returned an invalid response object; "
                "billing state remains conservatively reserved"
            )
        if response.provider != provider or response.model != model:
            self._mark_uncertain(record.call_id, "MismatchedProviderResponse", now=now)
            raise ProviderContractError(
                "provider response identity does not match the authorized request; "
                "billing state remains conservatively reserved"
            )

        try:
            self._budget.settle(
                reservation.reservation_id,
                actual_eur=response.actual_cost_eur,
                now=now,
            )
        except BudgetInvariantError:
            self._finish_call(
                record.call_id,
                ProviderCallStatus.COMPLETED,
                error_type=None,
                now=now,
            )
            raise
        except Exception as exc:
            self._mark_uncertain(record.call_id, type(exc).__name__, now=now)
            raise ProviderDispatchUncertain(
                record.call_id, reservation.reservation_id
            ) from exc

        self._finish_call(
            record.call_id,
            ProviderCallStatus.COMPLETED,
            error_type=None,
            now=now,
        )
        return PaidCallResult(
            call_id=record.call_id,
            reservation_id=reservation.reservation_id,
            response=response,
        )

    def budget_snapshot(self, *, now: datetime | None = None) -> BudgetSnapshot:
        return self._budget.snapshot(now=now)

    def _validate_quote(self, request: ModelRequest, quote: object) -> None:
        if not isinstance(quote, CostQuote):
            raise ProviderContractError("provider adapter returned an invalid cost quote")
        if quote.provider != request.provider.strip() or quote.model != request.model.strip():
            raise ProviderContractError(
                "provider quote identity does not match the requested provider/model"
            )

    def _ensure_call_record(
        self,
        *,
        key: str,
        reservation_id: UUID,
        request_fingerprint: str,
        provider: str,
        model: str,
        now: datetime | None,
    ) -> _CallRecord:
        timestamp = _timestamp(now)
        with self._database.immediate_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM provider_calls WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                call_id = uuid4()
                connection.execute(
                    """
                    INSERT INTO provider_calls(
                        call_id, idempotency_key, reservation_id, request_fingerprint,
                        provider, model, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?)
                    """,
                    (
                        str(call_id),
                        key,
                        str(reservation_id),
                        request_fingerprint,
                        provider,
                        model,
                        timestamp,
                    ),
                )
                return _CallRecord(
                    call_id=call_id,
                    reservation_id=reservation_id,
                    request_fingerprint=request_fingerprint,
                    status=ProviderCallStatus.PREPARED,
                )

            record = _row_to_call(row)
            if (
                record.reservation_id != reservation_id
                or record.request_fingerprint != request_fingerprint
            ):
                raise ProviderContractError(
                    "idempotency key is already bound to a different paid call"
                )
            if record.status is not ProviderCallStatus.PREPARED:
                raise ProviderReplayBlocked(
                    f"paid call is already in durable status {record.status}; retry blocked"
                )
            return record

    def _begin_dispatch(self, call_id: UUID, *, now: datetime | None) -> None:
        with self._database.immediate_transaction() as connection:
            row = connection.execute(
                "SELECT status FROM provider_calls WHERE call_id = ?",
                (str(call_id),),
            ).fetchone()
            if row is None:
                raise ProviderContractError(f"missing provider call record: {call_id}")
            status = ProviderCallStatus(str(row["status"]))
            if status is not ProviderCallStatus.PREPARED:
                raise ProviderReplayBlocked(
                    f"paid call is already in durable status {status}; retry blocked"
                )
            connection.execute(
                """
                UPDATE provider_calls
                SET status='dispatching', dispatch_started_at=?
                WHERE call_id=?
                """,
                (_timestamp(now), str(call_id)),
            )

    def _mark_uncertain(
        self, call_id: UUID, error_type: str, *, now: datetime | None
    ) -> None:
        try:
            self._finish_call(
                call_id,
                ProviderCallStatus.UNCERTAIN,
                error_type=error_type,
                now=now,
            )
        except Exception:
            # Leaving the record in DISPATCHING is even more conservative and
            # still prevents replay, so never hide the original provider error.
            pass

    def _finish_call(
        self,
        call_id: UUID,
        status: ProviderCallStatus,
        *,
        error_type: str | None,
        now: datetime | None,
    ) -> None:
        if status not in {
            ProviderCallStatus.COMPLETED,
            ProviderCallStatus.NOT_SENT,
            ProviderCallStatus.UNCERTAIN,
        }:
            raise ValueError(f"invalid terminal provider status: {status}")
        with self._database.immediate_transaction() as connection:
            connection.execute(
                """
                UPDATE provider_calls
                SET status=?, terminal_at=?, error_type=?
                WHERE call_id=?
                """,
                (status, _timestamp(now), error_type, str(call_id)),
            )


def _normalized_request(request: ModelRequest) -> ModelRequest:
    provider = request.provider.strip()
    model = request.model.strip()
    if provider == request.provider and model == request.model:
        return request
    return ModelRequest(
        task_id=request.task_id,
        provider=provider,
        model=model,
        input_text=request.input_text,
        max_output_tokens=request.max_output_tokens,
        metadata=request.metadata,
    )


def _build_registry(
    adapters: Iterable[PaidProviderAdapter],
) -> dict[str, PaidProviderAdapter]:
    registry: dict[str, PaidProviderAdapter] = {}
    for adapter in adapters:
        name = adapter.name.strip()
        if not name:
            raise ValueError("provider adapter name must not be empty")
        if name in registry:
            raise ValueError(f"duplicate provider adapter: {name}")
        registry[name] = adapter
    return registry


def _request_fingerprint(request: ModelRequest, quote: CostQuote) -> str:
    payload = {
        "task_id": str(request.task_id),
        "provider": request.provider.strip(),
        "model": request.model.strip(),
        "input_text": request.input_text,
        "max_output_tokens": request.max_output_tokens,
        "metadata": dict(sorted(request.metadata.items())),
        "quote_reference": quote.reference,
        "worst_case_eur": str(quote.worst_case_eur),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _row_to_call(row: sqlite3.Row) -> _CallRecord:
    return _CallRecord(
        call_id=UUID(str(row["call_id"])),
        reservation_id=UUID(str(row["reservation_id"])),
        request_fingerprint=str(row["request_fingerprint"]),
        status=ProviderCallStatus(str(row["status"])),
    )


def _timestamp(now: datetime | None) -> str:
    value = now or datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC).isoformat()
