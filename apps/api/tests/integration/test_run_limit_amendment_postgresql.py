from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import insert, select, text

from launchscope_api.infrastructure.db.schema import (
    agentteams_task_delivery,
    evaluation_run,
    matrix_event_receipt,
    matrix_handoff,
    outbox_message,
    run_execution_control,
    run_manifest,
    task,
    usage_record,
)
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.evaluation.agentteams_usage import AgentUsageSnapshot
from launchscope_api.modules.evaluation.canonical_event_recovery import CanonicalEventRecoveryApplication
from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_api.modules.evaluation.handoff_application import HandoffApplication
from launchscope_api.modules.evaluation.limit_amendment_application import RunLimitAmendmentApplication
from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.supervisor.matrix_adapter import PostgresV4DeliverySettlement


class _Directory:
    def agent_for_mxid(self, mxid: str) -> str | None:
        return mxid.removeprefix("@").split(":", 1)[0] if mxid.startswith("@") else None


class _Objects:
    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str:
        assert object_key.endswith(".json") and mime_type == "application/json" and payload
        return hashlib.sha256(payload).hexdigest()


def test_demo_amendment_reprocesses_the_exact_budget_blocked_event_without_mutating_manifest(
    monkeypatch, database, runtime_engine, tenant_records
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_DEMO_MODE", "true")
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "limit-amendment-integration-secret")
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    actor = Actor(tenant_id, "local-demo:budget-owner")
    sessions = session_factory(runtime_engine)
    with database.begin() as connection:
        connection.execute(text("UPDATE evaluation_run SET status='PLANNED' WHERE id=:id"), {"id": run_id})

    DispatchApplication(sessions)._dispatch_legacy_for_historical_tests_only(
        actor, run_id, idempotency_key="limit-amendment-dispatch"
    )
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        task_id, dispatch_epoch = session.execute(
            select(task.c.id, task.c.dispatch_epoch).where(
                task.c.run_id == run_id, task.c.stage_code == "LEADER_PLANNING"
            )
        ).one()
        original_manifest = session.execute(
            select(run_manifest.c.frozen_config).where(run_manifest.c.run_id == run_id)
        ).scalar_one()

    event = {
        "event_id": "$budget-amendment-result",
        "room_id": "!run:local",
        "sender": "@evaluation-manager:local",
        "content": {
            "schema_version": "1.0",
            "tenant_id": str(tenant_id),
            "run_id": str(run_id),
            "task_id": str(task_id),
            "agent_code": "evaluation-manager",
            "status": "SUCCEEDED",
            "dimension": "CONTROL",
            "claims": [],
            "evidence_refs": [],
            "risk": "LOW",
            "confidence": 0.7,
            "needs_human_approval": False,
            "next_action": "Delegate domain tasks",
            "provider_usage": {
                "receipt_id": "budget-amendment-provider-result",
                "input_tokens": 5_500_001,
                "output_tokens": 0,
                "cost_usd": "0.10",
                "submission_known": True,
                "usage_known": True,
            },
        },
    }
    handoffs = HandoffApplication(
        sessions, _Objects(), _Directory(), require_provider_usage=True  # type: ignore[arg-type]
    )
    blocked = handoffs.consume(actor, event, run_id=run_id, task_id=UUID(str(task_id)))
    assert blocked.task_status == "NEEDS_ATTENTION"

    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        control_epoch = session.execute(
            select(run_execution_control.c.control_epoch).where(run_execution_control.c.run_id == run_id)
        ).scalar_one()

    amended = RunLimitAmendmentApplication(sessions).amend(
        actor,
        run_id,
        task_id=UUID(str(task_id)),
        matrix_event_id="$budget-amendment-result",
        expected_control_epoch=int(control_epoch),
        expected_dispatch_epoch=int(dispatch_epoch),
        expected_amendment_version=0,
        model_calls=300,
        input_tokens=6_000_000,
        output_tokens=500_000,
        reason="Product owner authorized additional company API capacity for the same Demo Run",
        idempotency_key="limit-amendment-command",
        correlation_id=UUID("5e4b8d7a-3529-4a43-9f8c-e5c4f7c86b42"),
    )
    assert amended.amendment_version == 1
    assert amended.effective_limits == {
        "model_calls": 300,
        "input_tokens": 6_000_000,
        "output_tokens": 500_000,
    }

    replayed = handoffs.consume(actor, event, run_id=run_id, task_id=UUID(str(task_id)))
    assert replayed.task_status == "SUCCEEDED"
    assert replayed.duplicate is False

    duplicate = handoffs.consume(actor, event, run_id=run_id, task_id=UUID(str(task_id)))
    assert duplicate.duplicate is True
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        assert session.execute(
            select(run_manifest.c.frozen_config).where(run_manifest.c.run_id == run_id)
        ).scalar_one() == original_manifest
        assert session.execute(
            select(usage_record.c.quantity).where(
                usage_record.c.run_id == run_id,
                usage_record.c.idempotency_key == "provider:budget-amendment-provider-result",
            )
        ).scalar_one() == 5_500_001


def test_generation_v4_settlement_posts_copaw_delivery_usage_once(
    monkeypatch, database, runtime_engine, tenant_records
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "v4-settlement-integration-secret")
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    actor = Actor(tenant_id, "agentteams-matrix-bridge")
    sessions = session_factory(runtime_engine)
    with database.begin() as connection:
        connection.execute(text("UPDATE evaluation_run SET status='PLANNED' WHERE id=:id"), {"id": run_id})
    DispatchApplication(sessions)._dispatch_legacy_for_historical_tests_only(
        actor, run_id, idempotency_key="v4-settlement-dispatch"
    )
    now = datetime.now(UTC)
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        task_id, dispatch_epoch = session.execute(
            select(task.c.id, task.c.dispatch_epoch).where(
                task.c.run_id == run_id, task.c.stage_code == "LEADER_PLANNING"
            )
        ).one()
        session.execute(
            insert(agentteams_task_delivery).values(
                id=uuid4(),
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=task_id,
                dispatch_epoch=dispatch_epoch,
                agent_code="evaluation-manager",
                worker_name="evaluation-manager",
                room_id="!manager:local",
                assignment_event_id="$assignment-v4-settlement",
                status="DELIVERED",
                max_model_calls=20,
                accounting_mode="COPAW_TASK_DELTA",
                usage_baseline=AgentUsageSnapshot(100, 20, 2).to_dict(),
                delivered_at=now,
                deadline_at=now + timedelta(hours=1),
                completed_at=None,
            )
        )

    class UsageReader:
        def snapshot(self, agent_code: str) -> AgentUsageSnapshot:
            assert agent_code == "evaluation-manager"
            return AgentUsageSnapshot(400, 120, 5)

    settlement = PostgresV4DeliverySettlement(sessions, UsageReader())
    settlement.prepare(actor, run_id, UUID(str(task_id)))
    settlement.prepare(actor, run_id, UUID(str(task_id)))

    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        records = dict(
            session.execute(
                select(usage_record.c.category, usage_record.c.quantity).where(
                    usage_record.c.tenant_id == tenant_id,
                    usage_record.c.run_id == run_id,
                    usage_record.c.task_id == task_id,
                    usage_record.c.category.in_(("model", "model_calls")),
                )
            ).all()
        )
    assert records == {"model": 400, "model_calls": 3}


def test_demo_canonical_event_recovery_reopens_only_the_settled_exact_event_without_dispatch(
    monkeypatch, database, runtime_engine, tenant_records
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_DEMO_MODE", "true")
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    actor = Actor(tenant_id, "local-demo:canonical-recovery-owner")
    sessions = session_factory(runtime_engine)
    with database.begin() as connection:
        connection.execute(text("UPDATE evaluation_run SET status='PLANNED' WHERE id=:id"), {"id": run_id})
    DispatchApplication(sessions)._dispatch_legacy_for_historical_tests_only(
        actor, run_id, idempotency_key="canonical-recovery-dispatch"
    )
    now = datetime.now(UTC)
    payload_sha256 = "a" * 64
    event_id = "$settled-canonical-auditor-result"
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        task_id, dispatch_epoch = session.execute(
            select(task.c.id, task.c.dispatch_epoch).where(
                task.c.run_id == run_id, task.c.stage_code == "LEADER_PLANNING"
            )
        ).one()
        control_epoch = session.execute(
            select(run_execution_control.c.control_epoch).where(run_execution_control.c.run_id == run_id)
        ).scalar_one()
        outbox_before = session.execute(
            select(outbox_message.c.id).where(outbox_message.c.tenant_id == tenant_id)
        ).all()
        session.execute(
            insert(matrix_event_receipt).values(
                id=uuid4(), tenant_id=tenant_id, run_id=run_id, task_id=task_id,
                room_id="!auditor:local", matrix_event_id=event_id, sender_mxid="@evidence-auditor:local",
                payload_sha256=payload_sha256, processing_status="PROCESSED", created_at=now,
            )
        )
        session.execute(
            insert(matrix_handoff).values(
                id=uuid4(), tenant_id=tenant_id, run_id=run_id, task_id=task_id,
                room_id="!auditor:local", sender_agent="evidence-auditor", receiver_agent="evaluation-manager",
                kind="AUDIT_RESULT", finding_id=None, evidence_ids=[], risk="HIGH", confidence=0,
                approval_required=True, payload_sha256=payload_sha256, created_at=now,
            )
        )
        for category, quantity in (("model", 1234), ("model_calls", 7)):
            session.execute(
                insert(usage_record).values(
                    id=uuid4(), tenant_id=tenant_id, run_id=run_id, task_id=task_id,
                    category=category, quantity=quantity, cost=0,
                    idempotency_key=f"canonical-recovery-{category}", created_at=now,
                )
            )
        session.execute(
            task.update().where(task.c.tenant_id == tenant_id, task.c.id == task_id).values(
                status="NEEDS_ATTENTION", last_failure_class="SUBMISSION_UNKNOWN",
                last_error="provider receipt was reused by a different Matrix event", updated_at=now,
            )
        )
        session.execute(
            evaluation_run.update().where(
                evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id
            ).values(
                status="NEEDS_ATTENTION", last_failure_class="SUBMISSION_UNKNOWN",
                attention_reason="provider receipt was reused by a different Matrix event", updated_at=now,
            )
        )

    projection = CanonicalEventRecoveryApplication(sessions).recover(
        actor,
        run_id,
        task_id=UUID(str(task_id)),
        matrix_event_id=event_id,
        expected_control_epoch=int(control_epoch),
        expected_dispatch_epoch=int(dispatch_epoch),
        reason="Use the already settled canonical auditor result without another model call",
        idempotency_key="canonical-event-recovery-command",
        correlation_id=UUID("6264f649-77fe-4e98-a0a4-060d2a957de7"),
    )
    assert projection.dispatch_epoch == dispatch_epoch
    assert projection.control_epoch == control_epoch

    replay = CanonicalEventRecoveryApplication(sessions).recover(
        actor,
        run_id,
        task_id=UUID(str(task_id)),
        matrix_event_id=event_id,
        expected_control_epoch=int(control_epoch),
        expected_dispatch_epoch=int(dispatch_epoch),
        reason="Use the already settled canonical auditor result without another model call",
        idempotency_key="canonical-event-recovery-command",
        correlation_id=UUID("6264f649-77fe-4e98-a0a4-060d2a957de7"),
    )
    assert replay == projection
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        current = session.execute(
            select(task.c.status, task.c.dispatch_epoch).where(task.c.id == task_id)
        ).one()
        assert current == ("RUNNING", dispatch_epoch)
        assert session.execute(
            select(run_execution_control.c.control_epoch).where(run_execution_control.c.run_id == run_id)
        ).scalar_one() == control_epoch
        assert session.execute(
            select(outbox_message.c.id).where(outbox_message.c.tenant_id == tenant_id)
        ).all() == outbox_before
