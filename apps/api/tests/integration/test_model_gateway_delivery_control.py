from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, update

from launchscope_api.infrastructure.db.schema import (
    agentteams_task_delivery,
    budget_reservation,
    evaluation_run,
    physical_worker_execution_lease,
    run_manifest,
    stage,
    task,
)
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.evaluation.agentteams_delivery import AgentWorkerBusy, prepare_worker_lease
from launchscope_api.modules.evaluation.execution_control import (
    ExecutionControlApplication,
    RunExecutionPausedError,
    admit_model_invocation,
    mark_model_invocation_submitted,
)
from launchscope_api.modules.evaluation.model_capability import issue_delivery_capability
from launchscope_api.modules.identity_tenant.application import Actor

from .conftest import seed_tenant


def _gateway_task(database, records, *, worker_name: str = "shared-product-worker") -> tuple[UUID, UUID]:
    tenant_id = UUID(str(records["tenant_id"]))
    run_id = UUID(str(records["run_id"]))
    stage_id, task_id, delivery_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    with database.begin() as connection:
        connection.execute(update(evaluation_run).where(
            evaluation_run.c.tenant_id == tenant_id,
            evaluation_run.c.id == run_id,
        ).values(status="RUNNING", current_stage="DOMAIN_REVIEW", updated_at=now))
        connection.execute(insert(stage).values(
            id=stage_id,
            tenant_id=tenant_id,
            run_id=run_id,
            code="DOMAIN_REVIEW",
            ordinal=1,
            status="RUNNING",
            started_at=now,
        ))
        connection.execute(insert(task).values(
            id=task_id,
            tenant_id=tenant_id,
            run_id=run_id,
            stage_id=stage_id,
            stage_code="DOMAIN_REVIEW",
            agent_identity_ref="product-engineering@4.0",
            skill_ref="product-engineering",
            skill_version="4.0",
            status="RUNNING",
            idempotency_key=f"gateway-task:{task_id}",
            dependencies=[],
            tool_allowlist=[],
            timeout_seconds=600,
            success_condition={},
            required=True,
            correction_attempts=0,
            transient_retries=0,
            dispatch_epoch=0,
            side_effect_started=True,
            created_at=now,
            updated_at=now,
        ))
        connection.execute(insert(run_manifest).values(
            run_id=run_id,
            tenant_id=tenant_id,
            frozen_config={
                "limits": {"model_calls": 256, "input_tokens": 5_000_000, "output_tokens": 500_000},
                "model_runtime": {"allowed_model_ids": ["qwen3.8-max"]},
                "model_accounting": {"mode": "GATEWAY_DELIVERY"},
                "model_pricing": {"cost_mode": "TOKEN_ONLY"},
            },
            manifest_sha256="a" * 64,
            budget={},
            security_policy={},
            created_at=now,
        ))
        connection.execute(insert(budget_reservation).values(
            id=uuid4(),
            tenant_id=tenant_id,
            run_id=run_id,
            category="run_total",
            currency="USD",
            limit_amount=20,
            reserved_amount=20,
            consumed_amount=0,
            released_amount=0,
            status="RESERVED",
            idempotency_key=f"gateway-budget:{run_id}",
            created_at=now,
            updated_at=now,
        ))
        connection.execute(insert(agentteams_task_delivery).values(
            id=delivery_id,
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=task_id,
            dispatch_epoch=0,
            agent_code="product-engineering",
            worker_name=worker_name,
            room_id="!gateway:local",
            assignment_event_id=f"$assignment-{delivery_id}",
            status="DELIVERED",
            max_model_calls=20,
            accounting_mode="GATEWAY_DELIVERY",
            delivered_at=now,
            deadline_at=now + timedelta(minutes=10),
        ))
    return task_id, delivery_id


def test_physical_worker_lease_is_global_across_tenants(database, tenant_records, monkeypatch) -> None:
    other_records = seed_tenant(database)
    worker_name = f"shared-product-worker-{uuid4()}"
    first_task_id, _ = _gateway_task(database, tenant_records, worker_name=worker_name)
    second_task_id, _ = _gateway_task(database, other_records, worker_name=worker_name)
    monkeypatch.setenv(
        "LAUNCHSCOPE_AGENTTEAMS_WORKER_NAMES_JSON",
        json.dumps({"product-engineering": worker_name}),
    )

    with database.begin() as connection:
        first = prepare_worker_lease(
            connection,
            tenant_id=UUID(str(tenant_records["tenant_id"])),
            run_id=UUID(str(tenant_records["run_id"])),
            task_id=first_task_id,
            dispatch_epoch=0,
            control_epoch=0,
            agent_code="product-engineering",
            capability=issue_delivery_capability(),
        )
    with database.begin() as connection, pytest.raises(AgentWorkerBusy):
        prepare_worker_lease(
            connection,
            tenant_id=UUID(str(other_records["tenant_id"])),
            run_id=UUID(str(other_records["run_id"])),
            task_id=second_task_id,
            dispatch_epoch=0,
            control_epoch=0,
            agent_code="product-engineering",
            capability=issue_delivery_capability(),
        )

    with database.begin() as connection:
        connection.execute(update(physical_worker_execution_lease).where(
            physical_worker_execution_lease.c.id == first.lease_id,
        ).values(state="RELEASED", released_at=datetime.now(UTC), updated_at=datetime.now(UTC)))
        second = prepare_worker_lease(
            connection,
            tenant_id=UUID(str(other_records["tenant_id"])),
            run_id=UUID(str(other_records["run_id"])),
            task_id=second_task_id,
            dispatch_epoch=0,
            control_epoch=0,
            agent_code="product-engineering",
            capability=issue_delivery_capability(),
        )
    assert first.worker_name == second.worker_name == worker_name


def test_pause_waits_for_previously_admitted_call_and_blocks_later_admission(
    database,
    runtime_engine,
    tenant_records,
) -> None:
    tenant_id = UUID(str(tenant_records["tenant_id"]))
    run_id = UUID(str(tenant_records["run_id"]))
    race_worker = f"race-product-worker-{uuid4()}"
    task_id, delivery_id = _gateway_task(database, tenant_records, worker_name=race_worker)
    now = datetime.now(UTC)
    with database.begin() as connection:
        connection.execute(insert(physical_worker_execution_lease).values(
            id=uuid4(),
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=task_id,
            delivery_id=delivery_id,
            dispatch_epoch=0,
            control_epoch=0,
            agent_code="product-engineering",
            worker_name=race_worker,
            state="ACTIVE",
            credential_sha256=uuid4().hex + uuid4().hex,
            credential_expires_at=now + timedelta(minutes=10),
            prepared_at=now,
            activated_at=now,
            created_at=now,
            updated_at=now,
        ))

    sessions = session_factory(runtime_engine)
    admitted = False

    def admit() -> None:
        nonlocal admitted
        with tenant_transaction(sessions, tenant_records["scope"]) as session:
            invocation = admit_model_invocation(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=task_id,
                delivery_id=delivery_id,
                dispatch_epoch=0,
                agent_code="product-engineering",
                expected_epoch=0,
                model="qwen3.8-max",
                request_sha256="c" * 64,
            )
            mark_model_invocation_submitted(session, invocation.invocation_id)
            admitted = True
            time.sleep(0.25)

    with ThreadPoolExecutor(max_workers=2) as pool:
        admission_future = pool.submit(admit)
        deadline = time.monotonic() + 2
        while not admitted and time.monotonic() < deadline:
            time.sleep(0.01)
        pause_future = pool.submit(
            ExecutionControlApplication(sessions).pause,
            Actor(tenant_id, "owner"),
            run_id,
            expected_control_epoch=0,
            reason="USER_EXIT",
            idempotency_key="pause-race",
            correlation_id=uuid4(),
        )
        admission_future.result(timeout=3)
        paused = pause_future.result(timeout=3)

    assert paused.state == "PAUSE_REQUESTED"
    assert paused.in_flight_count == 1
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        with pytest.raises(RunExecutionPausedError, match="RUN_PAUSED"):
            admit_model_invocation(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=task_id,
                delivery_id=delivery_id,
                dispatch_epoch=0,
                agent_code="product-engineering",
                expected_epoch=1,
                model="qwen3.8-max",
                request_sha256="d" * 64,
            )
        assert session.execute(select(physical_worker_execution_lease.c.state)).scalar_one() == "DRAINING"
