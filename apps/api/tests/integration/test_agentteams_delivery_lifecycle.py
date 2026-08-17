from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import insert, select, update

from launchscope_api.infrastructure.db.schema import (
    agentteams_task_delivery,
    budget_reservation,
    evaluation_run,
    model_invocation,
    outbox_message,
    requirement_brief,
    task,
    usage_record,
)
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.evaluation.agentteams_delivery import (
    complete_task_delivery,
    reconcile_expired_task_deliveries,
    reconcile_expired_undelivered_tasks,
    record_task_delivery,
)
from launchscope_api.modules.evaluation.agentteams_usage import AgentUsageSnapshot
from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_api.modules.identity_tenant.application import Actor


def _prepare_supervisor_dispatch(database, tenant_records) -> None:
    now = datetime.now(UTC)
    brief_id = tenant_records["run_id"]
    with database.begin() as connection:
        connection.execute(update(evaluation_run).where(
            evaluation_run.c.id == tenant_records["run_id"],
            evaluation_run.c.tenant_id == tenant_records["tenant_id"],
        ).values(
            status="PLANNED",
            state_flags={"architecture_generation": "supervisor-1p4-v1"},
        ))
        connection.execute(insert(requirement_brief).values(
            id=brief_id,
            tenant_id=tenant_records["tenant_id"],
            product_version_id=tenant_records["version_id"],
            revision=1,
            schema_version="1.0",
            raw_input_object_key=f"integration/{brief_id}.json",
            raw_input_sha256="a" * 64,
            document={"schema_version": "1.0", "brief_id": str(brief_id), "evaluation_mode": "FULL_POTENTIAL"},
            confirmation_required=False,
            status="READY_FOR_PLANNING",
            created_by="integration",
            created_at=now,
            confirmed_at=now,
        ))


def test_delivery_ack_starts_deadline_and_watchdog_converges_timeout(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "integration-test-capability-secret")
    monkeypatch.setenv("LAUNCHSCOPE_MODEL_INPUT_USD_PER_MILLION", "2")
    monkeypatch.setenv("LAUNCHSCOPE_MODEL_OUTPUT_USD_PER_MILLION", "8")
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    _prepare_supervisor_dispatch(database, tenant_records)
    sessions = session_factory(runtime_engine)
    DispatchApplication(sessions)._dispatch_legacy_for_historical_tests_only(
        Actor(tenant_id, "local-demo:test"), run_id, idempotency_key="delivery"
    )
    delivered_at = datetime.now(UTC) - timedelta(seconds=3601)

    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        leader = session.execute(select(task).where(
            task.c.tenant_id == tenant_id,
            task.c.run_id == run_id,
            task.c.stage_code == "LEADER_PLANNING",
        )).mappings().one()
        record_task_delivery(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=leader["id"],
            dispatch_epoch=0,
            agent_code="evaluation-manager",
            room_id="!task:local",
            assignment_event_id="$assignment",
            usage_baseline=AgentUsageSnapshot(100, 20, 1),
            delivered_at=delivered_at,
            timeout_seconds=600,
        )

    class UsageReader:
        def snapshot(self, agent_code: str) -> AgentUsageSnapshot:
            assert agent_code == "evaluation-manager"
            return AgentUsageSnapshot(180, 45, 3)

    with database.begin() as connection:
        timed_out_rooms = reconcile_expired_task_deliveries(
            connection,
            now=datetime.now(UTC),
            usage_reader=UsageReader(),
            require_provider_usage=True,
        )
        assert timed_out_rooms == [("!task:local", str(connection.execute(select(
            agentteams_task_delivery.c.id
        ).where(agentteams_task_delivery.c.task_id == leader["id"])).scalar_one()))]

    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        assert session.execute(select(task.c.status).where(task.c.id == leader["id"])).scalar_one() == "NEEDS_ATTENTION"
        assert session.execute(select(evaluation_run.c.status).where(
            evaluation_run.c.id == run_id
        )).scalar_one() == "NEEDS_ATTENTION"
        assert session.execute(select(agentteams_task_delivery.c.status).where(
            agentteams_task_delivery.c.task_id == leader["id"]
        )).scalar_one() == "TIMED_OUT"
        usage = session.execute(select(
            usage_record.c.category,
            usage_record.c.quantity,
            usage_record.c.cost,
        ).where(
            usage_record.c.run_id == run_id,
            usage_record.c.task_id == leader["id"],
        ).order_by(usage_record.c.category)).all()
        assert usage == [
            ("model", Decimal("105.000000"), Decimal("0.000000")),
            ("model_calls", 2, Decimal("0.000000")),
            ("model_cost_unavailable", Decimal("1.000000"), Decimal("0.000000")),
        ]
        consumed = session.execute(select(budget_reservation.c.consumed_amount).where(
            budget_reservation.c.run_id == run_id,
            budget_reservation.c.category == "run_total",
        )).scalar_one()
        assert consumed == Decimal("0.000000")


def test_completed_delivery_is_not_timed_out(database, runtime_engine, tenant_records, monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "integration-test-capability-secret")
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    _prepare_supervisor_dispatch(database, tenant_records)
    sessions = session_factory(runtime_engine)
    DispatchApplication(sessions)._dispatch_legacy_for_historical_tests_only(
        Actor(tenant_id, "local-demo:test"), run_id, idempotency_key="complete"
    )
    delivered_at = datetime.now(UTC) - timedelta(seconds=601)

    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        leader = session.execute(select(task).where(
            task.c.tenant_id == tenant_id,
            task.c.run_id == run_id,
            task.c.stage_code == "LEADER_PLANNING",
        )).mappings().one()
        record_task_delivery(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=leader["id"],
            dispatch_epoch=0,
            agent_code="evaluation-manager",
            room_id="!task:local",
            assignment_event_id="$assignment",
            usage_baseline=None,
            delivered_at=delivered_at,
            timeout_seconds=600,
        )
        complete_task_delivery(session, tenant_id, leader["id"], 0, datetime.now(UTC))

    with database.begin() as connection:
        assert reconcile_expired_task_deliveries(connection, now=datetime.now(UTC)) == []


def test_active_model_stream_extends_expired_delivery_deadline(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "integration-test-capability-secret")
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    _prepare_supervisor_dispatch(database, tenant_records)
    sessions = session_factory(runtime_engine)
    DispatchApplication(sessions)._dispatch_legacy_for_historical_tests_only(
        Actor(tenant_id, "local-demo:test"), run_id, idempotency_key="streaming"
    )
    reconcile_at = datetime.now(UTC)
    delivered_at = reconcile_at - timedelta(seconds=3601)

    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        leader = session.execute(select(task).where(
            task.c.tenant_id == tenant_id,
            task.c.run_id == run_id,
            task.c.stage_code == "LEADER_PLANNING",
        )).mappings().one()
        delivery_id = record_task_delivery(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=leader["id"],
            dispatch_epoch=0,
            agent_code="evaluation-manager",
            room_id="!task:local",
            assignment_event_id="$assignment",
            usage_baseline=None,
            delivered_at=delivered_at,
            timeout_seconds=600,
        )
        session.execute(insert(model_invocation).values(
            id=uuid4(),
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=leader["id"],
            delivery_id=delivery_id,
            agent_code="evaluation-manager",
            control_epoch=0,
            dispatch_epoch=0,
            invocation_seq=1,
            model="integration-model",
            status="SUBMITTED",
            delivery_status="STREAMING",
            request_sha256="a" * 64,
            budget_held_amount=Decimal("0"),
            started_at=delivered_at,
            submitted_at=delivered_at,
        ))

    with database.begin() as connection:
        assert reconcile_expired_task_deliveries(connection, now=reconcile_at) == []

    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        delivery = session.execute(select(
            agentteams_task_delivery.c.status,
            agentteams_task_delivery.c.deadline_at,
        ).where(agentteams_task_delivery.c.id == delivery_id)).one()
        assert delivery.status == "DELIVERED"
        assert delivery.deadline_at >= reconcile_at + timedelta(seconds=3600)
        assert session.execute(select(task.c.status).where(task.c.id == leader["id"])).scalar_one() == "RUNNING"
        assert session.execute(select(evaluation_run.c.status).where(
            evaluation_run.c.id == run_id
        )).scalar_one() == "RUNNING"


def test_expired_unclaimed_task_is_cancelled_as_known_unsubmitted(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "integration-test-capability-secret")
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    _prepare_supervisor_dispatch(database, tenant_records)
    sessions = session_factory(runtime_engine)
    DispatchApplication(sessions)._dispatch_legacy_for_historical_tests_only(
        Actor(tenant_id, "local-demo:test"), run_id, idempotency_key="undelivered"
    )
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        leader_id, timeout_seconds = session.execute(select(
            task.c.id,
            task.c.timeout_seconds,
        ).where(
            task.c.tenant_id == tenant_id,
            task.c.run_id == run_id,
            task.c.stage_code == "LEADER_PLANNING",
        )).one()
        reconcile_at = datetime.now(UTC) + timedelta(seconds=timeout_seconds + 1)

    with database.begin() as connection:
        reconciled = reconcile_expired_undelivered_tasks(
            connection,
            now=reconcile_at,
        )
        assert str(leader_id) in reconciled

    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        leader = session.execute(select(
            task.c.status,
            task.c.last_failure_class,
        ).where(task.c.id == leader_id)).one()
        run = session.execute(select(
            evaluation_run.c.status,
            evaluation_run.c.last_failure_class,
        ).where(evaluation_run.c.id == run_id)).one()
        message = session.execute(select(
            outbox_message.c.publish_status,
            outbox_message.c.attempts,
        ).where(
            outbox_message.c.tenant_id == tenant_id,
            outbox_message.c.aggregate_id == run_id,
            outbox_message.c.event_type == "evaluation.task.ready.v1",
        )).one()
        assert leader == ("NEEDS_ATTENTION", "RUNTIME_UNAVAILABLE")
        assert run == ("NEEDS_ATTENTION", "RUNTIME_UNAVAILABLE")
        assert message == ("CANCELLED", 0)
        assert session.execute(select(agentteams_task_delivery.c.id).where(
            agentteams_task_delivery.c.task_id == leader_id,
        )).scalar_one_or_none() is None


def test_expired_attempted_task_remains_submission_unknown(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "integration-test-capability-secret")
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    _prepare_supervisor_dispatch(database, tenant_records)
    sessions = session_factory(runtime_engine)
    DispatchApplication(sessions)._dispatch_legacy_for_historical_tests_only(
        Actor(tenant_id, "local-demo:test"), run_id, idempotency_key="attempted"
    )
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        leader_id, timeout_seconds = session.execute(select(
            task.c.id,
            task.c.timeout_seconds,
        ).where(
            task.c.tenant_id == tenant_id,
            task.c.run_id == run_id,
            task.c.stage_code == "LEADER_PLANNING",
        )).one()
        session.execute(update(outbox_message).where(
            outbox_message.c.tenant_id == tenant_id,
            outbox_message.c.aggregate_id == run_id,
            outbox_message.c.event_type == "evaluation.task.ready.v1",
        ).values(publish_status="CLAIMED", attempts=1, claimed_by="test-publisher", claimed_at=datetime.now(UTC)))
        reconcile_at = datetime.now(UTC) + timedelta(seconds=timeout_seconds + 1)

    with database.begin() as connection:
        assert str(leader_id) in reconcile_expired_undelivered_tasks(connection, now=reconcile_at)

    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        leader = session.execute(select(task.c.status, task.c.last_failure_class).where(task.c.id == leader_id)).one()
        run = session.execute(select(
            evaluation_run.c.status,
            evaluation_run.c.last_failure_class,
        ).where(evaluation_run.c.id == run_id)).one()
        message = session.execute(select(outbox_message.c.publish_status).where(
            outbox_message.c.tenant_id == tenant_id,
            outbox_message.c.aggregate_id == run_id,
            outbox_message.c.event_type == "evaluation.task.ready.v1",
        )).scalar_one()
        assert leader == ("NEEDS_ATTENTION", "SUBMISSION_UNKNOWN")
        assert run == ("NEEDS_ATTENTION", "SUBMISSION_UNKNOWN")
        assert message == "CLAIMED"
