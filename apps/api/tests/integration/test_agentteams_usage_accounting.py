from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from launchscope_api.infrastructure.db.schema import budget_reservation, task, usage_record
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.evaluation.agentteams_delivery import record_task_delivery
from launchscope_api.modules.evaluation.agentteams_usage import AgentUsageSnapshot
from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_api.modules.evaluation.handoff_application import HandoffApplication
from launchscope_api.modules.identity_tenant.application import Actor


class _Directory:
    def agent_for_mxid(self, mxid: str) -> str | None:
        return "evaluation-manager" if mxid == "@evaluation-manager:local" else None


class _Objects:
    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str:
        return hashlib.sha256(payload).hexdigest()


class _Usage:
    def snapshot(self, agent_code: str) -> AgentUsageSnapshot:
        assert agent_code == "evaluation-manager"
        return AgentUsageSnapshot(1600, 350, 5)


@pytest.mark.parametrize(
    ("cost_mode", "expected_cost", "expected_consumed", "cost_unavailable"),
    [("EXACT", "0.002400", "0.002400", False), ("TOKEN_ONLY", "0.000000", "0.000000", True)],
)
def test_copaw_counter_delta_records_usage_without_requiring_provider_cost(
    database, runtime_engine, tenant_records, monkeypatch,
    cost_mode, expected_cost, expected_consumed, cost_unavailable,
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "integration-test-capability-secret")
    monkeypatch.setenv("LAUNCHSCOPE_PROVIDER_COST_MODE", cost_mode)
    monkeypatch.setenv("LAUNCHSCOPE_MODEL_INPUT_USD_PER_MILLION", "2")
    monkeypatch.setenv("LAUNCHSCOPE_MODEL_OUTPUT_USD_PER_MILLION", "8")
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    with database.begin() as connection:
        connection.execute(text("UPDATE evaluation_run SET status='PLANNED' WHERE id=:id"), {"id": run_id})
    sessions = session_factory(runtime_engine)
    actor = Actor(tenant_id, "local-demo:test")
    DispatchApplication(sessions)._dispatch_legacy_for_historical_tests_only(
        actor, run_id, idempotency_key="usage-accounting"
    )

    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        leader = session.execute(select(task).where(
            task.c.tenant_id == tenant_id,
            task.c.run_id == run_id,
            task.c.stage_code == "LEADER_PLANNING",
        )).mappings().one()
        now = datetime.now(UTC)
        record_task_delivery(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=leader["id"],
            dispatch_epoch=0,
            agent_code="evaluation-manager",
            room_id="!leader:local",
            assignment_event_id="$assignment",
            usage_baseline=AgentUsageSnapshot(1000, 200, 3),
            delivered_at=now,
            timeout_seconds=600,
        )

    event = {
        "event_id": "$leader-result",
        "room_id": "!leader:local",
        "sender": "@evaluation-manager:local",
        "content": {
            "schema_version": "1.0",
            "tenant_id": str(tenant_id),
            "run_id": str(run_id),
            "task_id": str(leader["id"]),
            "dispatch_epoch": 0,
            "agent_code": "evaluation-manager",
            "status": "SUCCEEDED",
            "dimension": "CONTROL",
            "claims": [],
            "evidence_refs": [],
            "risk": "LOW",
            "confidence": 0.8,
            "needs_human_approval": False,
            "next_action": "Delegate domain review",
        },
    }
    result = HandoffApplication(
        sessions,
        _Objects(),
        _Directory(),
        require_provider_usage=True,
        usage_reader=_Usage(),
    ).consume(actor, event, run_id=run_id, task_id=leader["id"])  # type: ignore[arg-type]
    assert result.task_status == "SUCCEEDED"

    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        row = session.execute(select(
            usage_record.c.category,
            usage_record.c.quantity,
            usage_record.c.cost,
        ).where(
            usage_record.c.tenant_id == tenant_id,
            usage_record.c.run_id == run_id,
            usage_record.c.category == "model",
        )).one()
        assert tuple(map(str, row)) == ("model", "750.000000", expected_cost)
        assert str(session.execute(select(budget_reservation.c.consumed_amount).where(
            budget_reservation.c.tenant_id == tenant_id,
            budget_reservation.c.run_id == run_id,
        )).scalar_one()) == expected_consumed
        marker_count = session.execute(select(usage_record.c.id).where(
            usage_record.c.tenant_id == tenant_id,
            usage_record.c.run_id == run_id,
            usage_record.c.category == "model_cost_unavailable",
        )).scalars().all()
        assert bool(marker_count) is cost_unavailable
