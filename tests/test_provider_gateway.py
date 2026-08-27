import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from local_dev.budget import BudgetConflict, BudgetExceeded, BudgetInvariantError
from local_dev.core.contracts import ModelRequest
from local_dev.db import Database
from local_dev.providers.contracts import (
    CostQuote,
    ProviderNotSentError,
    ProviderResponse,
)
from local_dev.providers.gateway import (
    PaidCallGateway,
    ProviderContractError,
    ProviderDispatchUncertain,
    ProviderReplayBlocked,
)

NOW = datetime(2026, 8, 27, 20, 0, tzinfo=UTC)


class FakeAdapter:
    name = "fake"

    def __init__(
        self,
        *,
        worst_case: str = "1",
        actual: str = "0.4",
        error: Exception | None = None,
        response: object | None = None,
    ) -> None:
        self.worst_case = Decimal(worst_case)
        self.actual = Decimal(actual)
        self.error = error
        self.response = response
        self.quote_count = 0
        self.execute_count = 0
        self.seen_request: ModelRequest | None = None

    def quote(self, request: ModelRequest) -> CostQuote:
        self.quote_count += 1
        return CostQuote(
            provider="fake",
            model=request.model,
            worst_case_eur=self.worst_case,
            reference="fake-price-v1",
        )

    def execute(self, request: ModelRequest, quote: CostQuote) -> ProviderResponse:
        self.execute_count += 1
        self.seen_request = request
        if self.error is not None:
            raise self.error
        if self.response is not None:
            return self.response  # type: ignore[return-value]
        return ProviderResponse(
            provider="fake",
            model=request.model,
            output_text="ok",
            actual_cost_eur=self.actual,
        )


def make_request(*, text: str = "hello", provider: str = "fake") -> ModelRequest:
    return ModelRequest(
        task_id=uuid4(),
        provider=provider,
        model="model",
        input_text=text,
        max_output_tokens=100,
        metadata={"source": "test"},
    )


def make_gateway(
    tmp_path: Path,
    adapter: FakeAdapter,
    *,
    limit: str = "20",
) -> tuple[PaidCallGateway, Database]:
    database = Database(tmp_path / "state.db")
    database.migrate()
    return PaidCallGateway(database, Decimal(limit), [adapter]), database


def test_success_dispatches_once_and_settles_budget(tmp_path: Path) -> None:
    adapter = FakeAdapter(worst_case="2", actual="0.5")
    gateway, database = make_gateway(tmp_path, adapter)

    result = gateway.call(make_request(), idempotency_key="call-1", now=NOW)

    assert result.response.output_text == "ok"
    assert adapter.execute_count == 1
    snapshot = gateway.budget_snapshot(now=NOW)
    assert snapshot.spent_eur == Decimal("0.5")
    assert snapshot.reserved_eur == 0
    with database.connect() as connection:
        status = connection.execute("SELECT status FROM provider_calls").fetchone()[0]
    assert status == "completed"


def test_budget_rejection_happens_before_provider_execute(tmp_path: Path) -> None:
    adapter = FakeAdapter(worst_case="2")
    gateway, _ = make_gateway(tmp_path, adapter, limit="1")

    with pytest.raises(BudgetExceeded):
        gateway.call(make_request(), idempotency_key="call-1", now=NOW)

    assert adapter.execute_count == 0


def test_invalid_quote_identity_blocks_before_budget_reservation(tmp_path: Path) -> None:
    class BadQuoteAdapter(FakeAdapter):
        def quote(self, request: ModelRequest) -> CostQuote:
            return CostQuote("other", request.model, Decimal("1"), "bad")

    adapter = BadQuoteAdapter()
    gateway, database = make_gateway(tmp_path, adapter)

    with pytest.raises(ProviderContractError, match="quote identity"):
        gateway.call(make_request(), idempotency_key="call-1", now=NOW)

    assert adapter.execute_count == 0
    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0]
    assert count == 0


def test_provider_not_sent_releases_reservation(tmp_path: Path) -> None:
    adapter = FakeAdapter(error=ProviderNotSentError("transport not reached"))
    gateway, database = make_gateway(tmp_path, adapter)

    with pytest.raises(ProviderNotSentError):
        gateway.call(make_request(), idempotency_key="call-1", now=NOW)

    assert gateway.budget_snapshot(now=NOW).available_eur == Decimal("20")
    with database.connect() as connection:
        status = connection.execute("SELECT status FROM provider_calls").fetchone()[0]
    assert status == "not_sent"


def test_unknown_provider_error_holds_budget_and_blocks_replay(tmp_path: Path) -> None:
    adapter = FakeAdapter(error=RuntimeError("timeout"))
    gateway, database = make_gateway(tmp_path, adapter)
    request = make_request()

    with pytest.raises(ProviderDispatchUncertain):
        gateway.call(request, idempotency_key="call-1", now=NOW)

    assert gateway.budget_snapshot(now=NOW).reserved_eur == Decimal("1")
    adapter.error = None
    with pytest.raises(ProviderReplayBlocked):
        gateway.call(request, idempotency_key="call-1", now=NOW)
    assert adapter.execute_count == 1

    with database.connect() as connection:
        status = connection.execute("SELECT status FROM provider_calls").fetchone()[0]
    assert status == "uncertain"


def test_invalid_response_holds_budget_and_marks_uncertain(tmp_path: Path) -> None:
    adapter = FakeAdapter(response={"invalid": True})
    gateway, database = make_gateway(tmp_path, adapter)

    with pytest.raises(ProviderContractError, match="invalid response"):
        gateway.call(make_request(), idempotency_key="call-1", now=NOW)

    assert gateway.budget_snapshot(now=NOW).reserved_eur == Decimal("1")
    with database.connect() as connection:
        status = connection.execute("SELECT status FROM provider_calls").fetchone()[0]
    assert status == "uncertain"


def test_mismatched_response_identity_holds_budget(tmp_path: Path) -> None:
    adapter = FakeAdapter(
        response=ProviderResponse(
            provider="other",
            model="model",
            output_text="x",
            actual_cost_eur=Decimal("0.2"),
        )
    )
    gateway, _ = make_gateway(tmp_path, adapter)

    with pytest.raises(ProviderContractError, match="response identity"):
        gateway.call(make_request(), idempotency_key="call-1", now=NOW)

    assert gateway.budget_snapshot(now=NOW).reserved_eur == Decimal("1")


def test_concurrent_same_key_executes_provider_at_most_once(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    gateway, _ = make_gateway(tmp_path, adapter)
    request = make_request()

    def run() -> str:
        try:
            gateway.call(request, idempotency_key="call-1", now=NOW)
            return "ok"
        except ProviderReplayBlocked:
            return "replay"
        except BudgetConflict:
            return "budget-terminal"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result() for future in [pool.submit(run), pool.submit(run)]]

    assert adapter.execute_count == 1
    assert outcomes.count("ok") == 1
    assert len(outcomes) == 2


def test_different_request_same_active_key_is_rejected_without_redispatch(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter(error=RuntimeError("timeout"))
    gateway, _ = make_gateway(tmp_path, adapter)
    first = make_request(text="one")

    with pytest.raises(ProviderDispatchUncertain):
        gateway.call(first, idempotency_key="call-1", now=NOW)

    second = ModelRequest(
        task_id=first.task_id,
        provider="fake",
        model="model",
        input_text="two",
        max_output_tokens=100,
        metadata={"source": "test"},
    )
    adapter.error = None
    with pytest.raises(ProviderContractError, match="different paid call"):
        gateway.call(second, idempotency_key="call-1", now=NOW)
    assert adapter.execute_count == 1


def test_completed_idempotency_key_cannot_dispatch_again(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    gateway, _ = make_gateway(tmp_path, adapter)
    request = make_request()

    gateway.call(request, idempotency_key="call-1", now=NOW)

    with pytest.raises(BudgetConflict):
        gateway.call(request, idempotency_key="call-1", now=NOW)
    assert adapter.execute_count == 1


def test_actual_cost_above_quote_records_completed_call_and_budget_breach(
    tmp_path: Path,
) -> None:
    adapter = FakeAdapter(worst_case="1", actual="1.1")
    gateway, database = make_gateway(tmp_path, adapter)

    with pytest.raises(BudgetInvariantError):
        gateway.call(make_request(), idempotency_key="call-1", now=NOW)

    with database.connect() as connection:
        status = connection.execute("SELECT status FROM provider_calls").fetchone()[0]
    assert status == "completed"


def test_duplicate_adapter_names_are_rejected(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()

    with pytest.raises(ValueError, match="duplicate provider adapter"):
        PaidCallGateway(database, Decimal("20"), [FakeAdapter(), FakeAdapter()])


def test_unknown_provider_is_rejected_without_reservation(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    gateway, database = make_gateway(tmp_path, adapter)

    with pytest.raises(ProviderContractError, match="no paid provider adapter"):
        gateway.call(make_request(provider="missing"), idempotency_key="call-1", now=NOW)

    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0]
    assert count == 0


def test_provider_call_database_transition_is_fail_closed(tmp_path: Path) -> None:
    adapter = FakeAdapter(error=RuntimeError("x"))
    gateway, database = make_gateway(tmp_path, adapter)
    with pytest.raises(ProviderDispatchUncertain):
        gateway.call(make_request(), idempotency_key="call-1", now=NOW)

    with pytest.raises(sqlite3.IntegrityError, match="invalid provider call state transition"):
        with database.immediate_transaction() as connection:
            connection.execute(
                """
                UPDATE provider_calls
                SET status='dispatching', terminal_at=NULL, error_type=NULL
                """
            )


def test_provider_and_model_are_normalized_before_adapter_execution(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    gateway, _ = make_gateway(tmp_path, adapter)
    request = ModelRequest(
        task_id=uuid4(),
        provider=" fake ",
        model=" model ",
        input_text="hello",
        max_output_tokens=100,
    )

    gateway.call(request, idempotency_key="call-1", now=NOW)

    assert adapter.seen_request is not None
    assert adapter.seen_request.provider == "fake"
    assert adapter.seen_request.model == "model"
