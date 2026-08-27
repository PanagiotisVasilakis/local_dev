import inspect
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from local_dev.budget import BudgetGovernor
from local_dev.core.contracts import (
    EvidenceRef,
    ModelRequest,
    TaskSpec,
    VerificationResult,
    VerificationStatus,
)
from local_dev.db import Database
from local_dev.local_models import LocalGenerationRequest, LocalMessage, LocalMessageRole
from local_dev.local_models.openai_compatible import OpenAICompatibleLocalRuntime


def test_verification_string_pass_cannot_bypass_evidence_requirement() -> None:
    with pytest.raises(TypeError, match="VerificationStatus"):
        VerificationResult(status="passed", summary="fake pass")  # type: ignore[arg-type]


def test_verification_evidence_is_defensively_immutable_and_typed() -> None:
    evidence = EvidenceRef("pytest", "run:1")
    source = [evidence]
    result = VerificationResult(
        VerificationStatus.PASSED,
        "ok",
        source,  # type: ignore[arg-type]
    )
    source.clear()
    assert result.evidence == (evidence,)
    assert isinstance(result.evidence, tuple)

    with pytest.raises(TypeError, match="EvidenceRef"):
        VerificationResult(
            VerificationStatus.FAILED,
            "bad evidence",
            ["not evidence"],  # type: ignore[list-item,arg-type]
        )


def test_core_contracts_reject_runtime_type_confusion(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="mode"):
        TaskSpec("x", tmp_path.resolve(), mode="ask")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="task_id"):
        ModelRequest("not-a-uuid", "p", "m", "x", 1)  # type: ignore[arg-type]
    for value in (True, 1.5):
        with pytest.raises(ValueError, match="positive integer"):
            ModelRequest(uuid4(), "p", "m", "x", value)  # type: ignore[arg-type]


def test_budget_rejects_invalid_task_identity_before_persistence(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()
    governor = BudgetGovernor(database, Decimal("20"))

    with pytest.raises(TypeError, match="task_id"):
        governor.reserve(
            idempotency_key="bad",
            task_id="not-a-uuid",  # type: ignore[arg-type]
            provider="p",
            model="m",
            worst_case_eur=Decimal("1"),
            now=datetime(2026, 8, 31, 23, 59, tzinfo=UTC),
        )

    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0]
    assert count == 0


def test_budget_observes_clock_once_for_period_and_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import local_dev.budget as budget_module

    class FakeDateTime(datetime):
        calls = 0

        @classmethod
        def now(cls, tz: object = None) -> "FakeDateTime":
            cls.calls += 1
            if cls.calls == 1:
                return cls(2026, 8, 31, 23, 59, 59, 999999, tzinfo=UTC)
            return cls(2026, 9, 1, 0, 0, 0, 0, tzinfo=UTC)

    monkeypatch.setattr(budget_module, "datetime", FakeDateTime)
    database = Database(tmp_path / "state.db")
    database.migrate()
    governor = BudgetGovernor(database, Decimal("20"))

    reservation = governor.reserve(
        idempotency_key="boundary",
        task_id=uuid4(),
        provider="p",
        model="m",
        worst_case_eur=Decimal("1"),
    )

    assert FakeDateTime.calls == 1
    assert reservation.period_utc == "2026-08"
    with database.connect() as connection:
        row = connection.execute(
            "SELECT period_utc, created_at FROM budget_reservations WHERE reservation_id=?",
            (str(reservation.reservation_id),),
        ).fetchone()
    assert row["period_utc"] == "2026-08"
    assert row["created_at"].startswith("2026-08-31T23:59:59.999999")


def test_budget_rejects_falsy_non_datetime_clock_value(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()
    governor = BudgetGovernor(database, Decimal("20"))

    with pytest.raises(TypeError, match="datetime"):
        governor.reserve(
            idempotency_key="bad-clock",
            task_id=uuid4(),
            provider="p",
            model="m",
            worst_case_eur=Decimal("1"),
            now=0,  # type: ignore[arg-type]
        )


def test_provider_call_insert_must_match_active_reservation(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.db")
    database.migrate()
    governor = BudgetGovernor(database, Decimal("20"))
    reservation = governor.reserve(
        idempotency_key="key",
        task_id=uuid4(),
        provider="provider",
        model="model",
        worst_case_eur=Decimal("1"),
        now=datetime(2026, 8, 27, 20, 0, tzinfo=UTC),
    )

    with pytest.raises(sqlite3.IntegrityError, match="active budget reservation"):
        with database.immediate_transaction() as connection:
            connection.execute(
                """
                INSERT INTO provider_calls(
                    call_id,idempotency_key,reservation_id,request_fingerprint,
                    provider,model,status,created_at
                ) VALUES (?,?,?,?,?,?,'prepared',?)
                """,
                (
                    str(uuid4()),
                    "wrong-key",
                    str(reservation.reservation_id),
                    "abc",
                    "provider",
                    "model",
                    "2026-08-27T20:00:00+00:00",
                ),
            )

    with database.immediate_transaction() as connection:
        connection.execute(
            """
            INSERT INTO provider_calls(
                call_id,idempotency_key,reservation_id,request_fingerprint,
                provider,model,status,created_at
            ) VALUES (?,?,?,?,?,?,'prepared',?)
            """,
            (
                str(uuid4()),
                "key",
                str(reservation.reservation_id),
                "abc",
                "provider",
                "model",
                "2026-08-27T20:00:00+00:00",
            ),
        )


def test_local_contracts_reject_invalid_runtime_types() -> None:
    messages = (LocalMessage(LocalMessageRole.USER, "hi"),)
    with pytest.raises(TypeError, match="task_id"):
        LocalGenerationRequest("x", "model", messages, 1)  # type: ignore[arg-type]
    for value in (True, 1.5):
        with pytest.raises(ValueError, match="positive integer"):
            LocalGenerationRequest(uuid4(), "model", messages, value)  # type: ignore[arg-type]


def test_local_runtime_public_constructor_cannot_inject_transport() -> None:
    signature = inspect.signature(OpenAICompatibleLocalRuntime)
    assert "transport" not in signature.parameters
    with pytest.raises(TypeError):
        OpenAICompatibleLocalRuntime(
            "http://127.0.0.1:11434/v1",
            transport=object(),  # type: ignore[call-arg]
        )
