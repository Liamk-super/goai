from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from launchscope_domain import (
    ApprovalBinding,
    BudgetError,
    BudgetReservation,
    DomainEvent,
    EventType,
    FailureClass,
)


def test_budget_cannot_be_over_reserved_or_over_consumed() -> None:
    reservation = BudgetReservation(uuid4(), "tokens", 10, 10)
    with pytest.raises(BudgetError):
        reservation.consume(11)


def test_approval_binding_is_one_time_and_parameter_bound() -> None:
    binding = ApprovalBinding(
        run_id=uuid4(),
        tool_id="research",
        parameters_sha256="d" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        one_time_token_id=uuid4(),
    )
    assert binding.binds("d" * 64)
    consumed = binding.consume()
    assert not consumed.binds("d" * 64)
    assert not binding.binds("e" * 64)


def test_event_envelope_preserves_correlation_and_fail_closed_payload() -> None:
    tenant_id, run_id, correlation_id = uuid4(), uuid4(), uuid4()
    event = DomainEvent(
        event_type=EventType.RUN_NEEDS_ATTENTION,
        tenant_id=tenant_id,
        run_id=run_id,
        correlation_id=correlation_id,
        idempotency_key="attention-1",
        payload={
            "reason_code": "provider_status_unknown",
            "failure_class": FailureClass.SUBMISSION_UNKNOWN,
            "retry_blocked": True,
        },
    )
    encoded = event.to_dict()
    assert encoded["event_type"] == "run.needs_attention"
    assert encoded["correlation_id"] == str(correlation_id)
    assert encoded["payload"]["retry_blocked"] is True
