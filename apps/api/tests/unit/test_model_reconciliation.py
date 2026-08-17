from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from launchscope_api.infrastructure.db.schema import (
    agentteams_task_delivery,
    budget_reservation,
    evaluation_run,
    metadata,
    model_invocation,
    model_usage_reconciliation,
    run_manifest,
    task,
    usage_record,
)
from launchscope_api.modules.evaluation.agentteams_usage import AgentUsageSnapshot
from launchscope_api.modules.evaluation.model_reconciliation import reconcile_gateway_delivery_usage


class _UsageReader:
    def __init__(self, snapshot: AgentUsageSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self, _agent_code: str) -> AgentUsageSnapshot:
        return self._snapshot


def _fixture():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    tenant_id, run_id, task_id, delivery_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    with sessions.begin() as session:
        session.execute(evaluation_run.insert().values(
            id=run_id, tenant_id=tenant_id, project_id=uuid4(), product_version_id=uuid4(),
            status="RUNNING", current_stage="DOMAIN_REVIEW", state_flags={}, standard_version="1.0",
            correlation_id=uuid4(), idempotency_key=f"run:{run_id}", run_kind="FULL_EVALUATION",
            created_at=now, updated_at=now,
        ))
        session.execute(task.insert().values(
            id=task_id, tenant_id=tenant_id, run_id=run_id, stage_id=uuid4(), stage_code="DOMAIN_REVIEW",
            agent_identity_ref="product-engineering@4.0", skill_ref="product", skill_version="1.0",
            status="RUNNING", idempotency_key=f"task:{task_id}", dependencies=[], tool_allowlist=[],
            timeout_seconds=600, success_condition={}, required=True, correction_attempts=0,
            transient_retries=0, dispatch_epoch=0, side_effect_started=True, created_at=now, updated_at=now,
        ))
        session.execute(run_manifest.insert().values(
            run_id=run_id, tenant_id=tenant_id,
            frozen_config={
                "limits": {"model_calls": 256, "input_tokens": 5_000_000, "output_tokens": 500_000},
                "model_pricing": {
                    "cost_mode": "EXACT",
                    "input_usd_per_million_tokens": "1",
                    "output_usd_per_million_tokens": "2",
                },
            },
            manifest_sha256="a" * 64, budget={}, security_policy={}, created_at=now,
        ))
        session.execute(budget_reservation.insert().values(
            id=uuid4(), tenant_id=tenant_id, run_id=run_id, category="run_total", currency="USD",
            limit_amount=20, reserved_amount=20, consumed_amount=0, released_amount=0,
            status="RESERVED", idempotency_key=f"budget:{run_id}", created_at=now, updated_at=now,
        ))
        session.execute(agentteams_task_delivery.insert().values(
            id=delivery_id, tenant_id=tenant_id, run_id=run_id, task_id=task_id, dispatch_epoch=0,
            agent_code="product-engineering", worker_name="product-engineering", room_id="!task:local",
            assignment_event_id="$assignment", status="DELIVERED", max_model_calls=20,
            accounting_mode="GATEWAY_DELIVERY", usage_baseline=AgentUsageSnapshot(10, 5, 1).to_dict(),
            delivered_at=now, deadline_at=now + timedelta(minutes=10),
        ))
        for sequence, prompt_tokens, completion_tokens in ((1, 40, 8), (2, 60, 12)):
            session.execute(model_invocation.insert().values(
                id=uuid4(), tenant_id=tenant_id, run_id=run_id, task_id=task_id,
                delivery_id=delivery_id, agent_code="product-engineering", control_epoch=0,
                dispatch_epoch=0, invocation_seq=sequence, model="qwen3.8-max", status="SETTLED",
                delivery_status="DELIVERED", request_sha256=str(sequence) * 64,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                budget_held_amount=0, started_at=now, settled_at=now,
            ))
    return sessions, tenant_id, run_id, task_id, delivery_id, now


def test_gateway_delivery_usage_posts_once_and_consumes_budget_once() -> None:
    sessions, tenant_id, run_id, task_id, delivery_id, now = _fixture()
    reader = _UsageReader(AgentUsageSnapshot(110, 25, 3))

    with sessions.begin() as session:
        first = reconcile_gateway_delivery_usage(
            session, tenant_id=tenant_id, run_id=run_id, task_id=task_id, dispatch_epoch=0,
            agent_code="product-engineering", now=now, usage_reader=reader,
        )
    with sessions.begin() as session:
        second = reconcile_gateway_delivery_usage(
            session, tenant_id=tenant_id, run_id=run_id, task_id=task_id, dispatch_epoch=0,
            agent_code="product-engineering", now=now, usage_reader=reader,
        )

    assert first.failure_class is None
    assert second.failure_class is None
    with sessions() as session:
        rows = session.execute(select(
            usage_record.c.category, usage_record.c.quantity, usage_record.c.cost,
        ).where(usage_record.c.run_id == run_id).order_by(usage_record.c.category)).all()
        assert rows == [
            ("model", Decimal("120.000000"), Decimal("0.000140")),
            ("model_calls", Decimal("2.000000"), Decimal("0.000000")),
        ]
        assert session.execute(select(budget_reservation.c.consumed_amount)).scalar_one() == Decimal("0.000140")
        reconciliation = session.execute(select(
            model_usage_reconciliation.c.state,
            model_usage_reconciliation.c.delivery_id,
        )).one()
        assert reconciliation == ("MATCHED", delivery_id)


def test_copaw_mismatch_is_advisory_and_does_not_duplicate_financial_usage() -> None:
    sessions, tenant_id, run_id, task_id, delivery_id, now = _fixture()

    with sessions.begin() as session:
        outcome = reconcile_gateway_delivery_usage(
            session, tenant_id=tenant_id, run_id=run_id, task_id=task_id, dispatch_epoch=0,
            agent_code="product-engineering", now=now,
            usage_reader=_UsageReader(AgentUsageSnapshot(999, 999, 99)),
        )

    assert outcome.failure_class is None
    with sessions() as session:
        row = session.execute(select(
            model_usage_reconciliation.c.state,
            model_usage_reconciliation.c.difference_reason,
        ).where(model_usage_reconciliation.c.delivery_id == delivery_id)).one()
        assert row[0] == "MISMATCH"
        assert "do not match" in row[1]
        assert len(session.execute(select(usage_record.c.id)).scalars().all()) == 2


def test_submission_unknown_is_quarantined_without_financial_posting() -> None:
    sessions, tenant_id, run_id, task_id, delivery_id, now = _fixture()
    with sessions.begin() as session:
        invocation_id = session.execute(select(model_invocation.c.id).limit(1)).scalar_one()
        session.execute(update(model_invocation).where(model_invocation.c.id == invocation_id).values(
            status="SUBMISSION_UNKNOWN",
            delivery_status="DELIVERY_UNKNOWN",
        ))
        outcome = reconcile_gateway_delivery_usage(
            session, tenant_id=tenant_id, run_id=run_id, task_id=task_id, dispatch_epoch=0,
            agent_code="product-engineering", now=now, usage_reader=None,
        )

    assert outcome.failure_class == "SUBMISSION_UNKNOWN"
    with sessions() as session:
        assert session.execute(select(model_usage_reconciliation.c.state)).scalar_one() == "UNKNOWN"
        assert session.execute(select(usage_record.c.id)).first() is None


def test_token_only_posts_explicit_cost_unavailable_receipt_once() -> None:
    sessions, tenant_id, run_id, task_id, delivery_id, now = _fixture()
    with sessions.begin() as session:
        manifest = session.execute(select(run_manifest.c.frozen_config)).scalar_one()
        manifest["model_pricing"] = {"cost_mode": "TOKEN_ONLY"}
        session.execute(update(run_manifest).values(frozen_config=manifest))
        outcome = reconcile_gateway_delivery_usage(
            session, tenant_id=tenant_id, run_id=run_id, task_id=task_id, dispatch_epoch=0,
            agent_code="product-engineering", now=now, usage_reader=None,
        )

    assert outcome.failure_class is None
    with sessions() as session:
        categories = set(session.execute(select(usage_record.c.category)).scalars())
        assert categories == {"model", "model_calls", "model_cost_unavailable"}
        assert session.execute(select(budget_reservation.c.consumed_amount)).scalar_one() == Decimal("0")
