"""T11 PostgreSQL budget reservation, audit and fail-closed evidence."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from launchscope_api.infrastructure.db.schema import audit_event, evaluation_run, usage_record
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.usage_quota.budget_application import BudgetApplication, BudgetExceeded, BudgetUnknown


def test_reserve_consume_release_are_idempotent_and_audited(runtime_engine, tenant_records) -> None:
    scope = tenant_records["scope"]
    app = BudgetApplication(session_factory(runtime_engine))
    reservation = app.reserve(
        scope,
        category="tool",
        limit=Decimal("20"),
        amount=Decimal("10"),
        currency="USD",
        idempotency_key="reserve-tool-1",
        actor_id="alice",
    )
    duplicate = app.reserve(
        scope,
        category="tool",
        limit=Decimal("20"),
        amount=Decimal("10"),
        currency="USD",
        idempotency_key="reserve-tool-1",
        actor_id="alice",
    )
    assert duplicate == reservation

    consumed = app.consume(
        scope,
        reservation.reservation_id,
        amount=Decimal("3.5"),
        idempotency_key="consume-tool-1",
        actor_id="worker",
    )
    again = app.consume(
        scope,
        reservation.reservation_id,
        amount=Decimal("3.5"),
        idempotency_key="consume-tool-1",
        actor_id="worker",
    )
    assert again.consumed == consumed.consumed == Decimal("3.500000")
    released = app.release(scope, reservation.reservation_id, actor_id="manager")
    assert released.released == Decimal("6.500000")
    with tenant_transaction(session_factory(runtime_engine), scope, actor_id="alice") as session:
        assert session.execute(select(func.count()).select_from(usage_record)).scalar_one() == 1
        actions = set(session.execute(select(audit_event.c.action)).scalars())
        assert {"budget.reserved", "budget.consumed", "budget.released"} <= actions


def test_budget_excess_and_unknown_cost_freeze_without_usage_or_retry(runtime_engine, tenant_records) -> None:
    scope = tenant_records["scope"]
    app = BudgetApplication(session_factory(runtime_engine))
    with pytest.raises(BudgetExceeded):
        app.reserve(
            scope,
            category="search",
            limit=Decimal("2"),
            amount=Decimal("3"),
            currency="USD",
            idempotency_key="reserve-over",
            actor_id="manager",
        )
    with tenant_transaction(session_factory(runtime_engine), scope, actor_id="alice") as session:
        assert (
            session.execute(select(evaluation_run.c.status).where(evaluation_run.c.id == scope.run_id)).scalar_one()
            == "WAITING_FOR_BUDGET"
        )

    reservation = app.reserve(
        scope,
        category="provider_cost",
        limit=Decimal("5"),
        amount=Decimal("5"),
        currency="USD",
        idempotency_key="reserve-provider",
        actor_id="manager",
    )
    with pytest.raises(BudgetUnknown):
        app.consume(
            scope,
            reservation.reservation_id,
            amount=None,
            idempotency_key="unknown-provider-cost",
            actor_id="worker",
        )
    with tenant_transaction(session_factory(runtime_engine), scope, actor_id="alice") as session:
        row = session.execute(
            select(
                evaluation_run.c.status, evaluation_run.c.last_failure_class, evaluation_run.c.attention_reason
            ).where(evaluation_run.c.id == scope.run_id)
        ).one()
        assert row.status == "NEEDS_ATTENTION"
        assert row.last_failure_class == "SUBMISSION_UNKNOWN"
        assert "frozen" in row.attention_reason
        assert session.execute(select(func.count()).select_from(usage_record)).scalar_one() == 0
