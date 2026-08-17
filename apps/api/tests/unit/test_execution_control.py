from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from launchscope_api.infrastructure.db.schema import (
    agent_plan,
    agentteams_task_delivery,
    evaluation_run,
    material,
    material_analysis,
    material_selection,
    material_selection_item,
    material_unit,
    metadata,
    model_invocation,
    outbox_message,
    physical_worker_execution_lease,
    run_control_request,
    run_execution_control,
    run_execution_event,
    run_manifest,
    run_status_history,
    skill_invocation,
    task,
    task_material_scope,
    tool_invocation,
)
from launchscope_api.modules.evaluation.agentteams_delivery import (
    reconcile_expired_task_deliveries,
    renew_worker_lease_credential,
)
from launchscope_api.modules.evaluation.execution_control import (
    ExecutionControlApplication,
    ModelAdmissionRejected,
    RunControlConflictError,
    RunNotPausableError,
    RunNotRecoverableError,
    admit_model_invocation,
    mark_model_invocation_submitted,
)
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_api.modules.user_validation.application import IdempotencyConflictError


def _fixture(monkeypatch):
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "unit-test-capability-secret")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    tenant_id, run_id, task_id, stage_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    with sessions.begin() as session:
        session.execute(
            evaluation_run.insert().values(
                id=run_id,
                tenant_id=tenant_id,
                project_id=uuid4(),
                product_version_id=uuid4(),
                status="RUNNING",
                current_stage="DOMAIN_REVIEW",
                state_flags={},
                standard_version="1.0",
                correlation_id=uuid4(),
                idempotency_key=f"run:{run_id}",
                run_kind="FULL_EVALUATION",
                created_at=now,
                updated_at=now,
            )
        )
        session.execute(
            run_execution_control.insert().values(
                id=uuid4(),
                tenant_id=tenant_id,
                run_id=run_id,
                state="ACTIVE",
                control_epoch=0,
                usage_settlement_status="NONE",
                in_flight_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.execute(
            run_manifest.insert().values(
                run_id=run_id,
                tenant_id=tenant_id,
                frozen_config={"limits": {}},
                manifest_sha256="a" * 64,
                budget={},
                security_policy={},
                created_at=now,
            )
        )
        session.execute(
            task.insert().values(
                id=task_id,
                tenant_id=tenant_id,
                run_id=run_id,
                stage_id=stage_id,
                stage_code="DOMAIN_REVIEW",
                agent_identity_ref="product-engineering@4.0",
                skill_ref="product-engineering",
                skill_version="4.0",
                status="READY",
                idempotency_key=f"task:{task_id}",
                dependencies=[],
                tool_allowlist=[],
                timeout_seconds=600,
                success_condition={},
                evidence_requirement="bounded evidence",
                required=True,
                correction_attempts=0,
                transient_retries=0,
                dispatch_epoch=0,
                side_effect_started=False,
                created_at=now,
                updated_at=now,
            )
        )
        session.execute(
            outbox_message.insert().values(
                id=uuid4(),
                tenant_id=tenant_id,
                aggregate_id=run_id,
                aggregate_type="evaluation_run",
                event_type="evaluation.task.ready.v1",
                event_id=uuid4(),
                schema_version="1.0",
                idempotency_key=f"task-ready:{task_id}",
                payload={"task_id": str(task_id)},
                publish_status="PENDING",
                available_at=now,
                attempts=0,
                occurred_at=now,
                created_at=now,
            )
        )
    return sessions, Actor(tenant_id, "owner"), run_id, task_id


def _activate_gateway_delivery(sessions, actor, run_id, task_id):
    now = datetime.now(UTC)
    delivery_id = uuid4()
    with sessions.begin() as session:
        manifest = session.execute(select(run_manifest.c.frozen_config)).scalar_one()
        manifest.update({
            "limits": {"model_calls": 256, "input_tokens": 5_000_000, "output_tokens": 500_000},
            "model_runtime": {"allowed_model_ids": ["qwen3.8-max"]},
            "model_accounting": {"mode": "GATEWAY_DELIVERY"},
        })
        session.execute(run_manifest.update().values(frozen_config=manifest))
        session.execute(task.update().where(task.c.id == task_id).values(status="RUNNING", side_effect_started=True))
        session.execute(agentteams_task_delivery.insert().values(
            id=delivery_id,
            tenant_id=actor.tenant_id,
            run_id=run_id,
            task_id=task_id,
            dispatch_epoch=0,
            agent_code="product-engineering",
            worker_name="worker-product-engineering",
            room_id="!task:local",
            assignment_event_id="$assignment",
            status="DELIVERED",
            max_model_calls=20,
            accounting_mode="GATEWAY_DELIVERY",
            delivered_at=now,
            deadline_at=now + timedelta(minutes=10),
        ))
        session.execute(physical_worker_execution_lease.insert().values(
            id=uuid4(),
            tenant_id=actor.tenant_id,
            run_id=run_id,
            task_id=task_id,
            delivery_id=delivery_id,
            dispatch_epoch=0,
            control_epoch=0,
            agent_code="product-engineering",
            worker_name="worker-product-engineering",
            state="ACTIVE",
            credential_sha256="b" * 64,
            credential_expires_at=now + timedelta(minutes=10),
            prepared_at=now,
            activated_at=now,
            created_at=now,
            updated_at=now,
        ))
    return delivery_id


def _mark_needs_attention(
    sessions,
    run_id,
    task_id,
    *,
    failure_class: str = "TIMEOUT",
    control_state: str = "ACTIVE",
) -> None:
    with sessions.begin() as session:
        session.execute(
            evaluation_run.update()
            .where(evaluation_run.c.id == run_id)
            .values(
                status="NEEDS_ATTENTION",
                last_failure_class=failure_class,
                attention_reason=f"Demo blocker: {failure_class}",
            )
        )
        session.execute(
            task.update()
            .where(task.c.id == task_id)
            .values(
                status="NEEDS_ATTENTION",
                last_failure_class=failure_class,
                last_error=f"Demo blocker: {failure_class}",
                side_effect_started=True,
            )
        )
        session.execute(
            run_execution_control.update()
            .where(run_execution_control.c.run_id == run_id)
            .values(
                state=control_state,
                usage_settlement_status="UNKNOWN" if control_state == "PAUSE_BLOCKED" else "NONE",
                last_error=f"Demo blocker: {failure_class}" if control_state == "PAUSE_BLOCKED" else None,
            )
        )


def test_demo_force_recovery_requeues_same_run_and_replays_idempotently(monkeypatch) -> None:
    sessions, actor, run_id, task_id = _fixture(monkeypatch)
    monkeypatch.setenv("LAUNCHSCOPE_DEMO_MODE", "true")
    _mark_needs_attention(sessions, run_id, task_id)
    application = ExecutionControlApplication(sessions)

    recovered = application.recover(
        actor,
        run_id,
        expected_control_epoch=0,
        force=True,
        idempotency_key="recover-timeout",
        correlation_id=uuid4(),
    )
    replayed = application.recover(
        actor,
        run_id,
        expected_control_epoch=0,
        force=True,
        idempotency_key="recover-timeout",
        correlation_id=uuid4(),
    )

    assert recovered.to_dict() == replayed.to_dict()
    assert recovered.run_status == "RUNNING"
    assert recovered.execution_control.control_epoch == 1
    assert recovered.recovered_task_ids == (task_id,)
    assert recovered.dispatched_task_count == 1
    with sessions() as session:
        stored_run = session.execute(
            select(
                evaluation_run.c.status,
                evaluation_run.c.last_failure_class,
                evaluation_run.c.attention_reason,
            ).where(evaluation_run.c.id == run_id)
        ).one()
        stored_task = session.execute(
            select(
                task.c.status,
                task.c.dispatch_epoch,
                task.c.last_failure_class,
                task.c.last_error,
                task.c.side_effect_started,
            ).where(task.c.id == task_id)
        ).one()
        messages = session.execute(
            select(outbox_message.c.publish_status, outbox_message.c.payload)
            .where(outbox_message.c.aggregate_id == run_id)
            .order_by(outbox_message.c.created_at, outbox_message.c.id)
        ).all()
        assert session.execute(select(run_control_request.c.id)).scalars().all()
        event = session.execute(select(run_execution_event)).mappings().one()
        assert event["event_type"] == "run.resumed"
        assert event["data"]["reason"].startswith("run.force_recovered:")
        assert session.execute(select(run_status_history.c.to_status)).scalar_one() == "RUNNING"
    assert stored_run == ("RUNNING", None, None)
    assert stored_task == ("READY", 1, None, None, False)
    assert [message[0] for message in messages] == ["CANCELLED", "PENDING"]
    pending_payload = next(payload for status, payload in messages if status == "PENDING")
    assert pending_payload["payload"]["dispatch_epoch"] == 1
    assert pending_payload["payload"]["control_epoch"] == 1


def test_demo_force_recovery_accepts_submission_unknown_and_preserves_completed_tasks(monkeypatch) -> None:
    sessions, actor, run_id, task_id = _fixture(monkeypatch)
    monkeypatch.setenv("LAUNCHSCOPE_DEMO_MODE", "true")
    _mark_needs_attention(
        sessions,
        run_id,
        task_id,
        failure_class="SUBMISSION_UNKNOWN",
        control_state="PAUSE_BLOCKED",
    )
    completed_task_id = uuid4()
    now = datetime.now(UTC)
    with sessions.begin() as session:
        session.execute(
            task.insert().values(
                id=completed_task_id,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                stage_id=uuid4(),
                stage_code="DOMAIN_REVIEW",
                agent_identity_ref="user-evidence@4.0",
                skill_ref="user-evidence",
                skill_version="4.0",
                status="SUCCEEDED",
                idempotency_key=f"task:{completed_task_id}",
                dependencies=[],
                tool_allowlist=[],
                timeout_seconds=600,
                success_condition={},
                evidence_requirement="bounded evidence",
                required=True,
                correction_attempts=0,
                transient_retries=0,
                dispatch_epoch=0,
                side_effect_started=True,
                created_at=now,
                updated_at=now,
            )
        )

    recovered = ExecutionControlApplication(sessions).recover(
        actor,
        run_id,
        expected_control_epoch=0,
        force=True,
        idempotency_key="recover-unknown",
        correlation_id=uuid4(),
    )

    assert recovered.recovered_task_ids == (task_id,)
    assert recovered.preserved_task_ids == (completed_task_id,)
    assert recovered.execution_control.state == "ACTIVE"
    assert recovered.execution_control.usage_settlement_status == "NONE"
    with sessions() as session:
        assert session.execute(select(task.c.status).where(task.c.id == completed_task_id)).scalar_one() == "SUCCEEDED"


def test_demo_force_recovery_requeues_known_failed_required_agent(monkeypatch) -> None:
    sessions, actor, run_id, task_id = _fixture(monkeypatch)
    monkeypatch.setenv("LAUNCHSCOPE_DEMO_MODE", "true")
    now = datetime.now(UTC)
    with sessions.begin() as session:
        version_id = session.execute(
            select(evaluation_run.c.product_version_id).where(evaluation_run.c.id == run_id)
        ).scalar_one()
        plan_id, material_id, analysis_id, unit_id, selection_id = (uuid4() for _ in range(5))
        session.execute(
            agent_plan.insert().values(
                id=plan_id,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                planning_task_id=task_id,
                dispatch_epoch=0,
                plan_version=1,
                evaluation_mode="FULL_POTENTIAL",
                raw_plan={},
                plan_sha256="1" * 64,
                status="ACCEPTED",
                created_at=now,
                decided_at=now,
            )
        )
        session.execute(
            material.insert().values(
                id=material_id,
                tenant_id=actor.tenant_id,
                product_version_id=version_id,
                source_type="UPLOAD",
                object_key=f"material/{material_id}.txt",
                sha256="2" * 64,
                size_bytes=10,
                mime_type="text/plain",
                display_name="required.txt",
                trust_level="T1",
                ingest_status="VALIDATED",
                object_metadata={},
                submitted_at=now,
                created_at=now,
            )
        )
        session.execute(
            material_analysis.insert().values(
                id=analysis_id,
                tenant_id=actor.tenant_id,
                material_id=material_id,
                product_version_id=version_id,
                status="READY",
                attempt=0,
                parser_version="test",
                page_count=1,
                unit_count=1,
                coverage={"uncovered_locators": []},
                external_consent=False,
                created_at=now,
                updated_at=now,
                completed_at=now,
            )
        )
        session.execute(
            material_unit.insert().values(
                id=unit_id,
                tenant_id=actor.tenant_id,
                analysis_id=analysis_id,
                material_id=material_id,
                product_version_id=version_id,
                ordinal=1,
                unit_type="PARAGRAPH",
                locator={"paragraph": 1},
                tags=[],
                confidence=1,
                contains_sensitive_data=False,
                object_key=f"unit/{unit_id}.json",
                sha256="3" * 64,
                summary="required material",
                created_at=now,
            )
        )
        session.execute(
            material_selection.insert().values(
                id=selection_id,
                tenant_id=actor.tenant_id,
                product_version_id=version_id,
                revision=1,
                idempotency_key=f"selection:{selection_id}",
                request_sha256="4" * 64,
                object_key=f"selection/{selection_id}.json",
                sha256="5" * 64,
                confirmed_by=actor.actor_id,
                confirmed_at=now,
                created_at=now,
            )
        )
        session.execute(
            material_selection_item.insert().values(
                id=uuid4(),
                tenant_id=actor.tenant_id,
                selection_id=selection_id,
                material_id=material_id,
                analysis_id=analysis_id,
                decision="INCLUDE",
                acknowledged_uncovered_locators=[],
                created_at=now,
            )
        )
        session.execute(
            evaluation_run.update()
            .where(evaluation_run.c.id == run_id)
            .values(
                status="NEEDS_ATTENTION",
                current_stage="NEEDS_ATTENTION",
                last_failure_class="BUDGET",
                attention_reason="model token limit reached",
            )
        )
        session.execute(
            task.update()
            .where(task.c.id == task_id)
            .values(
                status="KNOWN_FAILED",
                agent_identity_ref="product-engineering@6.0",
                last_failure_class="mcp_capability_token_malformed",
                last_error="known tool-input failure",
                side_effect_started=True,
            )
        )

    recovered = ExecutionControlApplication(sessions).recover(
        actor,
        run_id,
        expected_control_epoch=0,
        force=True,
        idempotency_key="recover-known-failed-agent",
        correlation_id=uuid4(),
    )

    assert recovered.recovered_task_ids == (task_id,)
    with sessions() as session:
        current_stage = session.execute(
            select(evaluation_run.c.current_stage).where(evaluation_run.c.id == run_id)
        ).scalar_one()
        stored = session.execute(
            select(
                task.c.status,
                task.c.dispatch_epoch,
                task.c.last_failure_class,
                task.c.tool_allowlist,
            ).where(task.c.id == task_id)
        ).one()
        scopes = session.execute(
            select(task_material_scope.c.material_id).where(task_material_scope.c.task_id == task_id)
        ).scalars().all()
    assert current_stage == "DOMAIN_REVIEW"
    assert stored == ("READY", 1, None, ["material.read.v1"])
    assert scopes == [material_id]


def test_demo_force_recovery_rejects_non_demo_and_stale_epoch(monkeypatch) -> None:
    sessions, actor, run_id, task_id = _fixture(monkeypatch)
    _mark_needs_attention(sessions, run_id, task_id)
    application = ExecutionControlApplication(sessions)

    monkeypatch.delenv("LAUNCHSCOPE_DEMO_MODE", raising=False)
    with pytest.raises(NotFoundError, match="disabled"):
        application.recover(
            actor,
            run_id,
            expected_control_epoch=0,
            force=True,
            idempotency_key="recover-disabled",
            correlation_id=uuid4(),
        )

    monkeypatch.setenv("LAUNCHSCOPE_DEMO_MODE", "true")
    with pytest.raises(RunControlConflictError, match="stale"):
        application.recover(
            actor,
            run_id,
            expected_control_epoch=3,
            force=True,
            idempotency_key="recover-stale",
            correlation_id=uuid4(),
        )
    with pytest.raises(RunNotRecoverableError, match="force"):
        application.recover(
            actor,
            run_id,
            expected_control_epoch=0,
            force=False,
            idempotency_key="recover-not-forced",
            correlation_id=uuid4(),
        )


def test_old_delivery_epoch_cannot_timeout_recovered_attempt(monkeypatch) -> None:
    sessions, actor, run_id, task_id = _fixture(monkeypatch)
    monkeypatch.setenv("LAUNCHSCOPE_DEMO_MODE", "true")
    _mark_needs_attention(sessions, run_id, task_id)
    expired_at = datetime.now(UTC) - timedelta(minutes=20)
    with sessions.begin() as session:
        session.execute(
            agentteams_task_delivery.insert().values(
                id=uuid4(),
                tenant_id=actor.tenant_id,
                run_id=run_id,
                task_id=task_id,
                dispatch_epoch=0,
                agent_code="product-engineering",
                worker_name="product-engineering",
                room_id="!old:local",
                assignment_event_id="$old",
                status="DELIVERED",
                max_model_calls=0,
                accounting_mode="COPAW_TASK_DELTA",
                delivered_at=expired_at,
                deadline_at=expired_at + timedelta(minutes=10),
            )
        )
    ExecutionControlApplication(sessions).recover(
        actor,
        run_id,
        expected_control_epoch=0,
        force=True,
        idempotency_key="recover-old-delivery",
        correlation_id=uuid4(),
    )
    with sessions.begin() as session:
        session.execute(task.update().where(task.c.id == task_id).values(status="RUNNING"))

    engine = sessions.kw["bind"]
    with engine.begin() as connection:
        assert reconcile_expired_task_deliveries(connection, now=datetime.now(UTC)) == []

    with sessions() as session:
        assert session.execute(select(task.c.status).where(task.c.id == task_id)).scalar_one() == "RUNNING"


def test_pause_holds_unsubmitted_work_and_resume_requires_explicit_command(monkeypatch) -> None:
    sessions, actor, run_id, _task_id = _fixture(monkeypatch)
    application = ExecutionControlApplication(sessions)

    paused = application.pause(
        actor,
        run_id,
        expected_control_epoch=0,
        reason="USER_EXIT",
        idempotency_key="pause-one",
        correlation_id=uuid4(),
    )

    assert paused.state == "PAUSED"
    assert paused.control_epoch == 1
    assert application.get(actor, run_id).state == "PAUSED"
    with sessions() as session:
        assert session.execute(select(outbox_message.c.publish_status)).scalar_one() == "HELD"

    resumed = application.resume(
        actor,
        run_id,
        expected_control_epoch=1,
        idempotency_key="resume-one",
        correlation_id=uuid4(),
    )
    assert resumed.state == "ACTIVE"
    assert resumed.control_epoch == 2
    with sessions() as session:
        statuses = set(session.execute(select(outbox_message.c.publish_status)).scalars())
        assert statuses == {"CANCELLED", "PENDING"}


def test_pause_interrupts_only_the_current_delivery_and_preserves_resumability(monkeypatch) -> None:
    sessions, actor, run_id, task_id = _fixture(monkeypatch)
    now = datetime.now(UTC)
    with sessions.begin() as session:
        session.execute(task.update().where(task.c.id == task_id).values(status="RUNNING", side_effect_started=True))
        session.execute(
            agentteams_task_delivery.insert().values(
                id=uuid4(),
                tenant_id=actor.tenant_id,
                run_id=run_id,
                task_id=task_id,
                dispatch_epoch=0,
                agent_code="product-engineering",
                worker_name="product-engineering",
                room_id="!task:local",
                assignment_event_id="$assignment",
                status="DELIVERED",
                max_model_calls=0,
                accounting_mode="COPAW_TASK_DELTA",
                delivered_at=now,
                deadline_at=now + timedelta(minutes=10),
            )
        )

    paused = ExecutionControlApplication(sessions).pause(
        actor,
        run_id,
        expected_control_epoch=0,
        reason="USER_EXIT",
        idempotency_key="pause-running",
        correlation_id=uuid4(),
    )

    assert paused.state == "PAUSED"
    assert paused.checkpoint is not None
    assert str(task_id) in paused.checkpoint["interrupted_task_ids"]
    with sessions() as session:
        stored_task = session.execute(select(task.c.status, task.c.dispatch_epoch).where(task.c.id == task_id)).one()
        delivery_status = session.execute(select(agentteams_task_delivery.c.status)).scalar_one()
    assert stored_task == ("READY", 1)
    assert delivery_status == "PAUSE_STOP_PENDING"


def test_one_admitted_model_call_settles_before_pause_becomes_effective(monkeypatch) -> None:
    sessions, actor, run_id, task_id = _fixture(monkeypatch)
    invocation_id = uuid4()
    with sessions.begin() as session:
        session.execute(task.update().where(task.c.id == task_id).values(status="RUNNING"))
        session.execute(
            model_invocation.insert().values(
                id=invocation_id,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                task_id=task_id,
                agent_code="product-engineering",
                control_epoch=0,
                model="fake-model",
                status="SUBMITTED",
                delivery_status="STREAMING",
                request_sha256="a" * 64,
                budget_held_amount=0,
                started_at=datetime.now(UTC),
            )
        )

    application = ExecutionControlApplication(sessions)
    requested = application.pause(
        actor,
        run_id,
        expected_control_epoch=0,
        reason="USER_EXIT",
        idempotency_key="pause-in-flight",
        correlation_id=uuid4(),
    )
    assert requested.state == "PAUSE_REQUESTED"
    assert requested.in_flight_count == 1

    with sessions.begin() as session:
        application.settle_invocation(
            session,
            invocation_id,
            status="SETTLED",
            prompt_tokens=10,
            completion_tokens=2,
        )

    settled = application.get(actor, run_id)
    assert settled.state == "PAUSED"
    assert settled.in_flight_count == 0


def test_delivery_admission_serializes_calls_and_allows_a_settled_fingerprint(monkeypatch) -> None:
    sessions, actor, run_id, task_id = _fixture(monkeypatch)
    delivery_id = _activate_gateway_delivery(sessions, actor, run_id, task_id)
    request_sha256 = "c" * 64

    with sessions.begin() as session:
        first = admit_model_invocation(
            session,
            tenant_id=actor.tenant_id,
            run_id=run_id,
            task_id=task_id,
            delivery_id=delivery_id,
            dispatch_epoch=0,
            agent_code="product-engineering",
            expected_epoch=0,
            model="qwen3.8-max",
            request_sha256=request_sha256,
        )
        with pytest.raises(ModelAdmissionRejected) as duplicate:
            admit_model_invocation(
                session,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                task_id=task_id,
                delivery_id=delivery_id,
                dispatch_epoch=0,
                agent_code="product-engineering",
                expected_epoch=0,
                model="qwen3.8-max",
                request_sha256=request_sha256,
            )
        assert duplicate.value.code == "DUPLICATE_OR_UNRESOLVED_MODEL_REQUEST"
        ExecutionControlApplication.settle_invocation(
            session,
            first.invocation_id,
            status="SETTLED",
            prompt_tokens=10,
            completion_tokens=2,
        )

    with sessions.begin() as session:
        second = admit_model_invocation(
            session,
            tenant_id=actor.tenant_id,
            run_id=run_id,
            task_id=task_id,
            delivery_id=delivery_id,
            dispatch_epoch=0,
            agent_code="product-engineering",
            expected_epoch=0,
            model="qwen3.8-max",
            request_sha256=request_sha256,
        )
    assert first.invocation_seq == 1
    assert second.invocation_seq == 2


def test_pause_drains_delivery_lease_and_reports_actual_parallel_inflight_count(monkeypatch) -> None:
    sessions, actor, run_id, task_id = _fixture(monkeypatch)
    delivery_id = _activate_gateway_delivery(sessions, actor, run_id, task_id)
    with sessions.begin() as session:
        invocation = admit_model_invocation(
            session,
            tenant_id=actor.tenant_id,
            run_id=run_id,
            task_id=task_id,
            delivery_id=delivery_id,
            dispatch_epoch=0,
            agent_code="product-engineering",
            expected_epoch=0,
            model="qwen3.8-max",
            request_sha256="d" * 64,
        )
        mark_model_invocation_submitted(session, invocation.invocation_id)

    result = ExecutionControlApplication(sessions).pause(
        actor,
        run_id,
        expected_control_epoch=0,
        reason="USER_EXIT",
        idempotency_key="pause-delivery-inflight",
        correlation_id=uuid4(),
    )

    assert result.state == "PAUSE_REQUESTED"
    assert result.in_flight_count == 1
    with sessions() as session:
        assert session.execute(select(physical_worker_execution_lease.c.state)).scalar_one() == "DRAINING"


def test_pause_rejects_locally_admitted_but_not_submitted_model_call(monkeypatch) -> None:
    sessions, actor, run_id, task_id = _fixture(monkeypatch)
    delivery_id = _activate_gateway_delivery(sessions, actor, run_id, task_id)
    with sessions.begin() as session:
        invocation = admit_model_invocation(
            session,
            tenant_id=actor.tenant_id,
            run_id=run_id,
            task_id=task_id,
            delivery_id=delivery_id,
            dispatch_epoch=0,
            agent_code="product-engineering",
            expected_epoch=0,
            model="qwen3.8-max",
            request_sha256="e" * 64,
        )

    result = ExecutionControlApplication(sessions).pause(
        actor,
        run_id,
        expected_control_epoch=0,
        reason="USER_EXIT",
        idempotency_key="pause-before-submission",
        correlation_id=uuid4(),
    )

    assert result.state == "PAUSED"
    assert result.in_flight_count == 0
    with sessions() as session:
        stored = session.execute(select(
            model_invocation.c.status,
            model_invocation.c.failure_class,
        ).where(model_invocation.c.id == invocation.invocation_id)).one()
        lease_state = session.execute(select(physical_worker_execution_lease.c.state)).scalar_one()
    assert stored == ("REJECTED", "PAUSED_BEFORE_SUBMISSION")
    assert lease_state == "RELEASED"


def test_delivery_credential_renewal_keeps_the_same_active_lease(monkeypatch) -> None:
    sessions, actor, run_id, task_id = _fixture(monkeypatch)
    delivery_id = _activate_gateway_delivery(sessions, actor, run_id, task_id)
    observed: list[tuple[str, str]] = []

    with sessions.begin() as session:
        lease_id, old_digest = session.execute(select(
            physical_worker_execution_lease.c.id,
            physical_worker_execution_lease.c.credential_sha256,
        )).one()
        renewed = renew_worker_lease_credential(
            session,
            lease_id,
            now=datetime.now(UTC),
            configure=lambda agent_code, token: observed.append((agent_code, token)),
        )

    assert renewed is True
    assert observed[0][0] == "product-engineering"
    assert observed[0][1].startswith("lsmg.v2.")
    with sessions() as session:
        lease = session.execute(select(
            physical_worker_execution_lease.c.delivery_id,
            physical_worker_execution_lease.c.state,
            physical_worker_execution_lease.c.credential_sha256,
        )).one()
    assert lease[0] == delivery_id
    assert lease[1] == "ACTIVE"
    assert lease[2] != old_digest
    assert observed[0][1] != lease[2]


def test_one_admitted_tool_call_settles_before_pause_becomes_effective(monkeypatch) -> None:
    sessions, actor, run_id, task_id = _fixture(monkeypatch)
    skill_invocation_id, tool_invocation_id = uuid4(), uuid4()
    with sessions.begin() as session:
        session.execute(task.update().where(task.c.id == task_id).values(status="RUNNING"))
        session.execute(
            skill_invocation.insert().values(
                id=skill_invocation_id,
                tenant_id=actor.tenant_id,
                task_id=task_id,
                skill_version_id=uuid4(),
                status="RUNNING",
                idempotency_key=f"skill:{task_id}",
                estimated_cost=0,
                created_at=datetime.now(UTC),
            )
        )
        session.execute(
            tool_invocation.insert().values(
                id=tool_invocation_id,
                tenant_id=actor.tenant_id,
                skill_invocation_id=skill_invocation_id,
                tool_code="public-research-search.v1",
                risk_tier="LOW",
                status="STARTED",
                parameters_sha256="b" * 64,
                created_at=datetime.now(UTC),
            )
        )

    application = ExecutionControlApplication(sessions)
    requested = application.pause(
        actor,
        run_id,
        expected_control_epoch=0,
        reason="USER_EXIT",
        idempotency_key="pause-tool-in-flight",
        correlation_id=uuid4(),
    )
    assert requested.state == "PAUSE_REQUESTED"
    assert requested.in_flight_count == 1

    with sessions.begin() as session:
        application.settle_tool_invocation(
            session,
            tenant_id=actor.tenant_id,
            run_id=run_id,
            invocation_id=tool_invocation_id,
            status="SUCCEEDED",
        )

    settled = application.get(actor, run_id)
    assert settled.state == "PAUSED"
    assert settled.in_flight_count == 0


def test_pause_is_idempotent_but_rejects_a_changed_request_hash(monkeypatch) -> None:
    sessions, actor, run_id, _task_id = _fixture(monkeypatch)
    application = ExecutionControlApplication(sessions)
    correlation_id = uuid4()

    first = application.pause(
        actor,
        run_id,
        expected_control_epoch=0,
        reason="USER_EXIT",
        idempotency_key="stable-pause-key",
        correlation_id=correlation_id,
    )
    replay = application.pause(
        actor,
        run_id,
        expected_control_epoch=0,
        reason="USER_EXIT",
        idempotency_key="stable-pause-key",
        correlation_id=correlation_id,
    )

    assert replay.to_dict() == first.to_dict()
    with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
        application.pause(
            actor,
            run_id,
            expected_control_epoch=1,
            reason="USER_EXIT",
            idempotency_key="stable-pause-key",
            correlation_id=correlation_id,
        )


def test_stale_epoch_and_terminal_run_are_rejected(monkeypatch) -> None:
    sessions, actor, run_id, _task_id = _fixture(monkeypatch)
    application = ExecutionControlApplication(sessions)

    with pytest.raises(RunControlConflictError, match="RUN_CONTROL_CONFLICT"):
        application.pause(
            actor,
            run_id,
            expected_control_epoch=7,
            reason="USER_EXIT",
            idempotency_key="stale-pause",
            correlation_id=uuid4(),
        )

    with sessions.begin() as session:
        session.execute(evaluation_run.update().where(evaluation_run.c.id == run_id).values(status="COMPLETED"))
        session.execute(run_execution_control.update().values(state="CLOSED"))
    with pytest.raises(RunNotPausableError, match="terminal Run"):
        application.pause(
            actor,
            run_id,
            expected_control_epoch=0,
            reason="USER_EXIT",
            idempotency_key="terminal-pause",
            correlation_id=uuid4(),
        )


def test_unknown_model_settlement_blocks_pause_without_retry(monkeypatch) -> None:
    sessions, actor, run_id, task_id = _fixture(monkeypatch)
    invocation_id = uuid4()
    with sessions.begin() as session:
        session.execute(task.update().where(task.c.id == task_id).values(status="RUNNING"))
        session.execute(
            model_invocation.insert().values(
                id=invocation_id,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                task_id=task_id,
                agent_code="product-engineering",
                control_epoch=0,
                model="fake-model",
                status="SUBMITTED",
                delivery_status="STREAMING",
                request_sha256="c" * 64,
                budget_held_amount=0,
                started_at=datetime.now(UTC),
            )
        )

    application = ExecutionControlApplication(sessions)
    assert application.pause(
        actor,
        run_id,
        expected_control_epoch=0,
        reason="USER_EXIT",
        idempotency_key="pause-unknown",
        correlation_id=uuid4(),
    ).state == "PAUSE_REQUESTED"
    with sessions.begin() as session:
        application.settle_invocation(
            session,
            invocation_id,
            status="SUBMISSION_UNKNOWN",
            error="provider receipt unavailable",
        )

    blocked = application.get(actor, run_id)
    assert blocked.state == "PAUSE_BLOCKED"
    assert blocked.usage_settlement_status == "UNKNOWN"
    with sessions() as session:
        run_state = session.execute(
            select(evaluation_run.c.status, evaluation_run.c.last_failure_class).where(evaluation_run.c.id == run_id)
        ).one()
        invocation_count = session.execute(select(model_invocation.c.id)).scalars().all()
    assert run_state == ("NEEDS_ATTENTION", "SUBMISSION_UNKNOWN")
    assert invocation_count == [invocation_id]
