"""Durable AgentTeams assignment acknowledgements and deadline reconciliation."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, and_, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from launchscope_api.infrastructure.db.schema import (
    agentteams_task_delivery,
    budget_reservation,
    evaluation_run,
    model_invocation,
    outbox_message,
    physical_worker_execution_lease,
    run_execution_control,
    run_manifest,
    run_status_history,
    stage,
    task,
    usage_record,
)

from .agentteams_usage import AgentUsageReader, AgentUsageSnapshot, usage_delta
from .model_capability import DeliveryCapability, issue_delivery_capability


class AgentWorkerBusy(RuntimeError):
    """A dedicated Worker already has an attributable Task interval open."""


AGENTTEAMS_EXECUTION_TIMEOUT_FLOOR_SECONDS = 3600


@dataclass(frozen=True)
class PreparedWorkerLease:
    lease_id: UUID
    delivery_id: UUID
    worker_name: str


def physical_worker_name(agent_code: str) -> str:
    raw = os.getenv("LAUNCHSCOPE_AGENTTEAMS_WORKER_NAMES_JSON", "").strip()
    if not raw:
        return agent_code
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LAUNCHSCOPE_AGENTTEAMS_WORKER_NAMES_JSON is malformed") from exc
    if not isinstance(document, dict) or not str(document.get(agent_code, "")).strip():
        raise RuntimeError(f"no physical Worker name configured for Agent {agent_code}")
    return str(document[agent_code]).strip()


def assert_agent_worker_available(
    session: Session | Connection,
    *,
    tenant_id: UUID,
    task_id: UUID,
    agent_code: str,
) -> None:
    worker_name = physical_worker_name(agent_code)
    lease = session.execute(
        select(physical_worker_execution_lease.c.id).where(
            physical_worker_execution_lease.c.worker_name == worker_name,
            physical_worker_execution_lease.c.state.in_(("PREPARING", "ACTIVE", "DRAINING")),
        ).limit(1)
    ).scalar_one_or_none()
    if lease is not None:
        raise AgentWorkerBusy(f"Physical Worker {worker_name} is leased to another delivery")
    active = session.execute(
        select(task.c.id)
        .select_from(
            task.join(
                evaluation_run,
                (evaluation_run.c.tenant_id == task.c.tenant_id)
                & (evaluation_run.c.id == task.c.run_id),
            )
        )
        .where(
            task.c.tenant_id == tenant_id,
            task.c.id != task_id,
            task.c.agent_identity_ref.like(f"{agent_code}@%"),
            task.c.status == "RUNNING",
            evaluation_run.c.status == "RUNNING",
        )
        .limit(1)
    ).scalar_one_or_none()
    if active is not None:
        raise AgentWorkerBusy(f"Agent {agent_code} is still running another attributable Task")


def prepare_worker_lease(
    session: Session | Connection,
    *,
    tenant_id: UUID,
    run_id: UUID,
    task_id: UUID,
    dispatch_epoch: int,
    control_epoch: int,
    agent_code: str,
    capability: DeliveryCapability,
    delivery_id: UUID | None = None,
) -> PreparedWorkerLease:
    control = session.execute(
        select(run_execution_control)
        .where(
            run_execution_control.c.tenant_id == tenant_id,
            run_execution_control.c.run_id == run_id,
        )
        .with_for_update()
    ).mappings().one_or_none()
    if control is None or control["state"] != "ACTIVE" or int(control["control_epoch"]) != control_epoch:
        raise AgentWorkerBusy("Run execution control changed before Worker lease preparation")
    worker_name = physical_worker_name(agent_code)
    assert_agent_worker_available(
        session,
        tenant_id=tenant_id,
        task_id=task_id,
        agent_code=agent_code,
    )
    lease_id = uuid4()
    prepared_delivery_id = delivery_id or uuid4()
    now = datetime.now(capability.expires_at.tzinfo)
    try:
        with session.begin_nested():
            session.execute(insert(physical_worker_execution_lease).values(
                id=lease_id,
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=task_id,
                delivery_id=prepared_delivery_id,
                dispatch_epoch=dispatch_epoch,
                control_epoch=control_epoch,
                agent_code=agent_code,
                worker_name=worker_name,
                state="PREPARING",
                credential_sha256=capability.sha256,
                credential_expires_at=capability.expires_at,
                prepared_at=now,
                activated_at=None,
                draining_at=None,
                released_at=None,
                last_error=None,
                created_at=now,
                updated_at=now,
            ))
    except IntegrityError as exc:
        raise AgentWorkerBusy(f"Physical Worker {worker_name} is leased to another delivery") from exc
    return PreparedWorkerLease(lease_id, prepared_delivery_id, worker_name)


def reconcile_stale_preparing_worker_leases(
    session: Session | Connection,
    *,
    now: datetime,
    grace_seconds: int = 30,
) -> list[UUID]:
    cutoff = now - timedelta(seconds=grace_seconds)
    rows = session.execute(
        select(physical_worker_execution_lease)
        .where(
            physical_worker_execution_lease.c.state == "PREPARING",
            physical_worker_execution_lease.c.prepared_at <= cutoff,
        )
        .with_for_update(skip_locked=True)
    ).mappings().all()
    affected: list[UUID] = []
    for lease in rows:
        reason = "Worker lease preparation expired with unknown Matrix assignment state; automatic retry prohibited"
        session.execute(update(physical_worker_execution_lease).where(
            physical_worker_execution_lease.c.id == lease["id"],
            physical_worker_execution_lease.c.state == "PREPARING",
        ).values(
            state="RELEASED",
            released_at=now,
            last_error=reason,
            updated_at=now,
        ))
        task_status = session.execute(select(task.c.status).where(
            task.c.tenant_id == lease["tenant_id"],
            task.c.id == lease["task_id"],
        ).with_for_update()).scalar_one_or_none()
        if task_status in {"READY", "RUNNING"}:
            session.execute(update(task).where(
                task.c.tenant_id == lease["tenant_id"],
                task.c.id == lease["task_id"],
            ).values(
                status="NEEDS_ATTENTION",
                side_effect_started=True,
                last_failure_class="SUBMISSION_UNKNOWN",
                last_error=reason,
                updated_at=now,
            ))
        run_status = session.execute(select(evaluation_run.c.status).where(
            evaluation_run.c.tenant_id == lease["tenant_id"],
            evaluation_run.c.id == lease["run_id"],
        ).with_for_update()).scalar_one_or_none()
        if run_status not in {None, "NEEDS_ATTENTION", "COMPLETED", "FAILED", "CANCELLED"}:
            session.execute(update(evaluation_run).where(
                evaluation_run.c.tenant_id == lease["tenant_id"],
                evaluation_run.c.id == lease["run_id"],
            ).values(
                status="NEEDS_ATTENTION",
                last_failure_class="SUBMISSION_UNKNOWN",
                attention_reason=reason,
                updated_at=now,
            ))
            session.execute(insert(run_status_history).values(
                id=uuid4(),
                tenant_id=lease["tenant_id"],
                run_id=lease["run_id"],
                from_status=run_status,
                to_status="NEEDS_ATTENTION",
                reason=reason,
                failure_class="SUBMISSION_UNKNOWN",
                occurred_at=now,
            ))
        affected.append(UUID(str(lease["delivery_id"])))
    return affected


def fail_worker_lease(session: Session, lease_id: UUID, *, error: str, now: datetime) -> None:
    session.execute(
        update(physical_worker_execution_lease)
        .where(
            physical_worker_execution_lease.c.id == lease_id,
            physical_worker_execution_lease.c.state == "PREPARING",
        )
        .values(state="RELEASED", released_at=now, last_error=error[:1000], updated_at=now)
    )


def due_worker_lease_renewal_ids(
    session: Session | Connection,
    *,
    now: datetime,
    renew_within_seconds: int = 600,
) -> list[UUID]:
    return [
        UUID(str(value))
        for value in session.execute(select(physical_worker_execution_lease.c.id).where(
            physical_worker_execution_lease.c.state == "ACTIVE",
            physical_worker_execution_lease.c.credential_expires_at <= now + timedelta(seconds=renew_within_seconds),
        )).scalars()
    ]


def renew_worker_lease_credential(
    session: Session | Connection,
    lease_id: UUID,
    *,
    now: datetime,
    configure: Callable[[str, str], None],
) -> bool:
    identity = session.execute(select(
        physical_worker_execution_lease.c.tenant_id,
        physical_worker_execution_lease.c.run_id,
    ).where(physical_worker_execution_lease.c.id == lease_id)).mappings().one_or_none()
    if identity is None:
        return False
    control = session.execute(select(run_execution_control).where(
        run_execution_control.c.tenant_id == identity["tenant_id"],
        run_execution_control.c.run_id == identity["run_id"],
    ).with_for_update()).mappings().one_or_none()
    if control is None or control["state"] != "ACTIVE":
        return False
    lease = session.execute(select(physical_worker_execution_lease).where(
        physical_worker_execution_lease.c.id == lease_id,
        physical_worker_execution_lease.c.tenant_id == identity["tenant_id"],
    ).with_for_update()).mappings().one_or_none()
    if lease is None or lease["state"] != "ACTIVE" or int(lease["control_epoch"]) != int(control["control_epoch"]):
        return False
    active_task = session.execute(select(task.c.id).where(
        task.c.tenant_id == lease["tenant_id"],
        task.c.id == lease["task_id"],
        task.c.status == "RUNNING",
        task.c.dispatch_epoch == lease["dispatch_epoch"],
    )).scalar_one_or_none()
    if active_task is None:
        return False
    capability = issue_delivery_capability()
    configure(str(lease["agent_code"]), capability.token)
    result: Any = session.execute(update(physical_worker_execution_lease).where(
        physical_worker_execution_lease.c.id == lease_id,
        physical_worker_execution_lease.c.state == "ACTIVE",
        physical_worker_execution_lease.c.credential_sha256 == lease["credential_sha256"],
    ).values(
        credential_sha256=capability.sha256,
        credential_expires_at=capability.expires_at,
        updated_at=now,
    ))
    return result.rowcount == 1


def drain_worker_lease_for_delivery(
    session: Session | Connection,
    delivery_id: UUID,
    *,
    now: datetime,
) -> None:
    active = int(session.execute(
        select(func.count())
        .select_from(model_invocation)
        .where(
            model_invocation.c.delivery_id == delivery_id,
            model_invocation.c.status.in_(("STARTED", "SUBMITTED")),
        )
    ).scalar_one())
    values = (
        {"state": "DRAINING", "draining_at": now, "updated_at": now}
        if active
        else {"state": "RELEASED", "draining_at": now, "released_at": now, "updated_at": now}
    )
    session.execute(
        update(physical_worker_execution_lease)
        .where(
            physical_worker_execution_lease.c.delivery_id == delivery_id,
            physical_worker_execution_lease.c.state.in_(("PREPARING", "ACTIVE", "DRAINING")),
        )
        .values(**values)
    )


def extended_delivery_deadline(
    *,
    delivered_at: datetime,
    configured_timeout_seconds: int,
    now: datetime,
    active_model: bool,
) -> datetime | None:
    execution_floor = delivered_at + timedelta(
        seconds=max(configured_timeout_seconds, AGENTTEAMS_EXECUTION_TIMEOUT_FLOOR_SECONDS)
    )
    if execution_floor > now:
        return execution_floor
    if active_model:
        return now + timedelta(seconds=AGENTTEAMS_EXECUTION_TIMEOUT_FLOOR_SECONDS)
    return None


def record_task_delivery(
    session: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    task_id: UUID,
    dispatch_epoch: int,
    agent_code: str,
    room_id: str,
    assignment_event_id: str,
    usage_baseline: AgentUsageSnapshot | None,
    delivered_at: datetime,
    timeout_seconds: int,
    delivery_id: UUID | None = None,
    worker_name: str | None = None,
    max_model_calls: int = 0,
    accounting_mode: str = "GATEWAY_DELIVERY",
    lease_id: UUID | None = None,
) -> UUID:
    run_flags = session.execute(select(evaluation_run.c.state_flags).where(
        evaluation_run.c.tenant_id == tenant_id,
        evaluation_run.c.id == run_id,
    ).with_for_update()).scalar_one()
    stage_id = session.execute(select(task.c.stage_id).where(
        task.c.tenant_id == tenant_id,
        task.c.run_id == run_id,
        task.c.id == task_id,
    )).scalar_one()
    recorded_delivery_id = delivery_id or uuid4()
    session.execute(insert(agentteams_task_delivery).values(
        id=recorded_delivery_id,
        tenant_id=tenant_id,
        run_id=run_id,
        task_id=task_id,
        dispatch_epoch=dispatch_epoch,
        agent_code=agent_code,
        worker_name=worker_name or agent_code,
        room_id=room_id,
        assignment_event_id=assignment_event_id,
        status="DELIVERED",
        max_model_calls=max_model_calls,
        accounting_mode=accounting_mode,
        usage_baseline=usage_baseline.to_dict() if usage_baseline else None,
        delivered_at=delivered_at,
        deadline_at=delivered_at + timedelta(seconds=timeout_seconds),
        completed_at=None,
    ))
    result: Any = session.execute(update(task).where(
        task.c.tenant_id == tenant_id,
        task.c.run_id == run_id,
        task.c.id == task_id,
        task.c.status == "READY",
        task.c.dispatch_epoch == dispatch_epoch,
    ).values(status="RUNNING", side_effect_started=True, updated_at=delivered_at))
    if result.rowcount != 1:
        raise RuntimeError("Matrix assignment was acknowledged for a Task that is not READY")
    session.execute(update(stage).where(
        stage.c.tenant_id == tenant_id,
        stage.c.run_id == run_id,
        stage.c.id == stage_id,
    ).values(status="RUNNING", started_at=delivered_at))
    session.execute(update(evaluation_run).where(
        evaluation_run.c.tenant_id == tenant_id,
        evaluation_run.c.id == run_id,
    ).values(
        state_flags={**(run_flags or {}), "dispatch_pending": False},
        updated_at=delivered_at,
    ))
    if lease_id is not None:
        activated: Any = session.execute(
            update(physical_worker_execution_lease)
            .where(
                physical_worker_execution_lease.c.id == lease_id,
                physical_worker_execution_lease.c.tenant_id == tenant_id,
                physical_worker_execution_lease.c.delivery_id == recorded_delivery_id,
                physical_worker_execution_lease.c.state == "PREPARING",
            )
            .values(state="ACTIVE", activated_at=delivered_at, updated_at=delivered_at)
        )
        if activated.rowcount != 1:
            raise RuntimeError("Worker lease could not be activated for the durable delivery")
    return recorded_delivery_id


def complete_task_delivery(
    session: Session,
    tenant_id: UUID,
    task_id: UUID,
    dispatch_epoch: int,
    completed_at: datetime,
) -> None:
    delivery_id = session.execute(select(agentteams_task_delivery.c.id).where(
        agentteams_task_delivery.c.tenant_id == tenant_id,
        agentteams_task_delivery.c.task_id == task_id,
        agentteams_task_delivery.c.dispatch_epoch == dispatch_epoch,
        agentteams_task_delivery.c.status == "DELIVERED",
    ).with_for_update()).scalar_one_or_none()
    session.execute(update(agentteams_task_delivery).where(
        agentteams_task_delivery.c.tenant_id == tenant_id,
        agentteams_task_delivery.c.task_id == task_id,
        agentteams_task_delivery.c.dispatch_epoch == dispatch_epoch,
        agentteams_task_delivery.c.status == "DELIVERED",
    ).values(status="COMPLETED", completed_at=completed_at))
    if delivery_id is not None:
        drain_worker_lease_for_delivery(session, UUID(str(delivery_id)), now=completed_at)


def task_usage_baseline(
    session: Session,
    tenant_id: UUID,
    task_id: UUID,
    dispatch_epoch: int,
) -> AgentUsageSnapshot | None:
    value = session.execute(select(agentteams_task_delivery.c.usage_baseline).where(
        agentteams_task_delivery.c.tenant_id == tenant_id,
        agentteams_task_delivery.c.task_id == task_id,
        agentteams_task_delivery.c.dispatch_epoch == dispatch_epoch,
    )).scalar_one_or_none()
    return AgentUsageSnapshot.from_dict(value) if value is not None else None


def _record_expired_task_usage(
    connection: Connection,
    row: dict[str, Any],
    *,
    now: datetime,
    usage_reader: AgentUsageReader | None,
) -> None:
    if row.get("accounting_mode") == "GATEWAY_DELIVERY":
        from .model_reconciliation import reconcile_gateway_delivery_usage

        outcome = reconcile_gateway_delivery_usage(
            connection,
            tenant_id=UUID(str(row["tenant_id"])),
            run_id=UUID(str(row["run_id"])),
            task_id=UUID(str(row["task_id"])),
            dispatch_epoch=int(row["dispatch_epoch"]),
            agent_code=str(row["agent_code"]),
            now=now,
            usage_reader=usage_reader,
        )
        if outcome.failure_class is not None:
            raise RuntimeError(outcome.reason or outcome.failure_class)
        return
    if usage_reader is None:
        raise RuntimeError("CoPaw usage reader is unavailable")
    baseline = AgentUsageSnapshot.from_dict(row["usage_baseline"])
    terminal = usage_reader.snapshot(str(row["agent_code"]))
    manifest = connection.execute(select(run_manifest.c.frozen_config).where(
        run_manifest.c.tenant_id == row["tenant_id"],
        run_manifest.c.run_id == row["run_id"],
    )).scalar_one()
    pricing = manifest["model_pricing"]
    cost_mode = str(pricing.get("cost_mode") or "TOKEN_ONLY").upper()
    if cost_mode not in {"EXACT", "TOKEN_ONLY"}:
        raise ValueError("Run Manifest contains an unsupported provider cost mode")
    prices = {}
    if cost_mode == "EXACT":
        prices = {
            "input_usd_per_million": Decimal(str(pricing["input_usd_per_million_tokens"])),
            "output_usd_per_million": Decimal(str(pricing["output_usd_per_million_tokens"])),
        }
    receipt = usage_delta(
        baseline,
        terminal,
        task_key=f"{row['task_id']}:{row['dispatch_epoch']}",
        **prices,
    )
    idempotency_key = f"provider:{receipt.receipt_id}"
    duplicate = connection.execute(select(usage_record.c.id).where(
        usage_record.c.tenant_id == row["tenant_id"],
        usage_record.c.idempotency_key == idempotency_key,
    )).scalar_one_or_none()
    if duplicate is not None:
        return
    connection.execute(insert(usage_record).values(
        id=uuid4(), tenant_id=row["tenant_id"], run_id=row["run_id"], task_id=row["task_id"],
        category="model", quantity=receipt.input_tokens + receipt.output_tokens,
        cost=receipt.cost_usd or 0, idempotency_key=idempotency_key, created_at=now,
    ))
    connection.execute(insert(usage_record).values(
        id=uuid4(), tenant_id=row["tenant_id"], run_id=row["run_id"], task_id=row["task_id"],
        category="model_calls", quantity=receipt.call_count, cost=0,
        idempotency_key=f"{idempotency_key}:calls", created_at=now,
    ))
    if receipt.cost_usd is None:
        connection.execute(insert(usage_record).values(
            id=uuid4(), tenant_id=row["tenant_id"], run_id=row["run_id"], task_id=row["task_id"],
            category="model_cost_unavailable", quantity=1, cost=0,
            idempotency_key=f"{idempotency_key}:cost-unavailable", created_at=now,
        ))
        return
    reservation = connection.execute(select(budget_reservation).where(
        budget_reservation.c.tenant_id == row["tenant_id"],
        budget_reservation.c.run_id == row["run_id"],
        budget_reservation.c.category == "run_total",
    ).with_for_update()).mappings().one()
    connection.execute(update(budget_reservation).where(
        budget_reservation.c.id == reservation["id"],
        budget_reservation.c.tenant_id == row["tenant_id"],
    ).values(
        consumed_amount=reservation["consumed_amount"] + receipt.cost_usd,
        status="CONSUMED",
        updated_at=now,
    ))


def reconcile_expired_task_deliveries(
    connection: Connection,
    *,
    now: datetime,
    usage_reader: AgentUsageReader | None = None,
    require_provider_usage: bool = False,
) -> list[tuple[str, str]]:
    rows = connection.execute(select(
        agentteams_task_delivery.c.id,
        agentteams_task_delivery.c.tenant_id,
        agentteams_task_delivery.c.run_id,
        agentteams_task_delivery.c.task_id,
        agentteams_task_delivery.c.dispatch_epoch,
        agentteams_task_delivery.c.agent_code,
        agentteams_task_delivery.c.accounting_mode,
        agentteams_task_delivery.c.usage_baseline,
        agentteams_task_delivery.c.room_id,
        agentteams_task_delivery.c.delivered_at,
        task.c.timeout_seconds,
    ).select_from(agentteams_task_delivery.join(task, and_(
        task.c.tenant_id == agentteams_task_delivery.c.tenant_id,
        task.c.id == agentteams_task_delivery.c.task_id,
        task.c.dispatch_epoch == agentteams_task_delivery.c.dispatch_epoch,
    ))).where(
        agentteams_task_delivery.c.status == "DELIVERED",
        agentteams_task_delivery.c.deadline_at <= now,
        task.c.status == "RUNNING",
    ).with_for_update(skip_locked=True)).mappings().all()
    affected_runs: set[tuple[UUID, UUID]] = set()
    timed_out_rooms: list[tuple[str, str]] = []
    for row in rows:
        active_model = connection.execute(select(model_invocation.c.id).where(
            model_invocation.c.tenant_id == row["tenant_id"],
            model_invocation.c.delivery_id == row["id"],
            model_invocation.c.status.in_(("STARTED", "SUBMITTED")),
        ).limit(1)).scalar_one_or_none() is not None
        extended_deadline = extended_delivery_deadline(
            delivered_at=row["delivered_at"],
            configured_timeout_seconds=int(row["timeout_seconds"]),
            now=now,
            active_model=active_model,
        )
        if extended_deadline is not None:
            connection.execute(update(agentteams_task_delivery).where(
                agentteams_task_delivery.c.id == row["id"],
                agentteams_task_delivery.c.status == "DELIVERED",
            ).values(deadline_at=extended_deadline))
            continue
        reason = "AgentTeams Task exceeded its delivered execution deadline; automatic retry prohibited"
        drain_worker_lease_for_delivery(connection, UUID(str(row["id"])), now=now)
        if row["accounting_mode"] == "GATEWAY_DELIVERY":
            try:
                _record_expired_task_usage(connection, dict(row), now=now, usage_reader=usage_reader)
            except Exception as exc:  # noqa: BLE001 - timeout must still converge when settlement fails.
                reason = f"{reason}; gateway usage accounting unavailable: {exc}"
        elif usage_reader is not None and row["usage_baseline"] is not None:
            try:
                _record_expired_task_usage(connection, dict(row), now=now, usage_reader=usage_reader)
            except Exception as exc:  # noqa: BLE001 - timeout must still converge when telemetry fails.
                if require_provider_usage:
                    reason = f"{reason}; provider usage accounting unavailable: {exc}"
        elif require_provider_usage:
            reason = f"{reason}; provider usage accounting unavailable: no delivery baseline"
        connection.execute(update(task).where(
            task.c.tenant_id == row["tenant_id"],
            task.c.id == row["task_id"],
            task.c.status == "RUNNING",
            task.c.dispatch_epoch == row["dispatch_epoch"],
        ).values(
            status="NEEDS_ATTENTION",
            last_failure_class="TIMEOUT",
            last_error=reason,
            updated_at=now,
        ))
        connection.execute(update(agentteams_task_delivery).where(
            agentteams_task_delivery.c.id == row["id"],
        ).values(status="TIMED_OUT", completed_at=now))
        timed_out_rooms.append((str(row["room_id"]), str(row["id"])))
        affected_runs.add((row["tenant_id"], row["run_id"]))
    for tenant_id, run_id in affected_runs:
        old_status = connection.execute(select(evaluation_run.c.status).where(
            evaluation_run.c.tenant_id == tenant_id,
            evaluation_run.c.id == run_id,
        ).with_for_update()).scalar_one()
        if old_status != "RUNNING":
            continue
        reason = "At least one AgentTeams Task exceeded its execution deadline"
        connection.execute(update(evaluation_run).where(
            evaluation_run.c.tenant_id == tenant_id,
            evaluation_run.c.id == run_id,
        ).values(
            status="NEEDS_ATTENTION",
            last_failure_class="TIMEOUT",
            attention_reason=reason,
            updated_at=now,
        ))
        connection.execute(insert(run_status_history).values(
            id=uuid4(),
            tenant_id=tenant_id,
            run_id=run_id,
            from_status=old_status,
            to_status="NEEDS_ATTENTION",
            reason=reason,
            failure_class="TIMEOUT",
            occurred_at=now,
        ))
    return timed_out_rooms


def reconcile_expired_undelivered_tasks(connection: Connection, *, now: datetime) -> list[str]:
    rows = connection.execute(select(
        task.c.id,
        task.c.tenant_id,
        task.c.run_id,
        task.c.dispatch_epoch,
        task.c.timeout_seconds,
        task.c.updated_at,
    ).select_from(task.join(evaluation_run, and_(
        evaluation_run.c.tenant_id == task.c.tenant_id,
        evaluation_run.c.id == task.c.run_id,
    ))).where(
        task.c.status == "READY",
        evaluation_run.c.status == "RUNNING",
    ).with_for_update(skip_locked=True)).mappings().all()
    expired = [
        row for row in rows
        if row["updated_at"] + timedelta(seconds=int(row["timeout_seconds"])) <= now
    ]
    affected_runs: dict[tuple[UUID, UUID], tuple[str, str]] = {}
    task_ids: list[str] = []
    unknown_reason = (
        "AgentTeams assignment did not produce a durable delivery receipt before its deadline; "
        "external submission state is unknown and automatic retry is prohibited"
    )
    unsubmitted_reason = (
        "AgentTeams execution services were unavailable until the dispatch deadline; "
        "the unclaimed transport message was cancelled without external submission"
    )
    for row in expired:
        delivered = connection.execute(select(agentteams_task_delivery.c.id).where(
            agentteams_task_delivery.c.tenant_id == row["tenant_id"],
            agentteams_task_delivery.c.task_id == row["id"],
            agentteams_task_delivery.c.dispatch_epoch == row["dispatch_epoch"],
        )).scalar_one_or_none()
        if delivered is not None:
            continue
        messages = connection.execute(select(outbox_message).where(
            outbox_message.c.tenant_id == row["tenant_id"],
            outbox_message.c.aggregate_id == row["run_id"],
            outbox_message.c.event_type == "evaluation.task.ready.v1",
        ).with_for_update()).mappings().all()
        message = next(
            (
                candidate
                for candidate in messages
                if str((candidate["payload"] or {}).get("task_id")) == str(row["id"])
                and int(((candidate["payload"] or {}).get("payload") or {}).get("dispatch_epoch", -1))
                == int(row["dispatch_epoch"])
            ),
            None,
        )
        known_unsubmitted = (
            message is not None
            and message["publish_status"] == "PENDING"
            and int(message["attempts"]) == 0
            and message["claimed_by"] is None
            and message["claimed_at"] is None
        )
        failure_class = "RUNTIME_UNAVAILABLE" if known_unsubmitted else "SUBMISSION_UNKNOWN"
        reason = unsubmitted_reason if known_unsubmitted else unknown_reason
        if known_unsubmitted and message is not None:
            cancelled: Any = connection.execute(update(outbox_message).where(
                outbox_message.c.id == message["id"],
                outbox_message.c.tenant_id == row["tenant_id"],
                outbox_message.c.publish_status == "PENDING",
                outbox_message.c.attempts == 0,
                outbox_message.c.claimed_by.is_(None),
                outbox_message.c.claimed_at.is_(None),
            ).values(publish_status="CANCELLED", last_error=reason))
            if cancelled.rowcount != 1:
                failure_class = "SUBMISSION_UNKNOWN"
                reason = unknown_reason
        result: Any = connection.execute(update(task).where(
            task.c.tenant_id == row["tenant_id"],
            task.c.id == row["id"],
            task.c.status == "READY",
        ).values(
            status="NEEDS_ATTENTION",
            last_failure_class=failure_class,
            last_error=reason,
            updated_at=now,
        ))
        if result.rowcount != 1:
            continue
        key = (row["tenant_id"], row["run_id"])
        current = affected_runs.get(key)
        if current is None or failure_class == "SUBMISSION_UNKNOWN":
            affected_runs[key] = (failure_class, reason)
        task_ids.append(str(row["id"]))
    for (tenant_id, run_id), (failure_class, reason) in affected_runs.items():
        run = connection.execute(select(
            evaluation_run.c.status,
            evaluation_run.c.state_flags,
        ).where(
            evaluation_run.c.tenant_id == tenant_id,
            evaluation_run.c.id == run_id,
        ).with_for_update()).mappings().one()
        if run["status"] != "RUNNING":
            continue
        connection.execute(update(evaluation_run).where(
            evaluation_run.c.tenant_id == tenant_id,
            evaluation_run.c.id == run_id,
        ).values(
            status="NEEDS_ATTENTION",
            state_flags={**(run["state_flags"] or {}), "dispatch_pending": False},
            last_failure_class=failure_class,
            attention_reason=reason,
            updated_at=now,
        ))
        connection.execute(insert(run_status_history).values(
            id=uuid4(),
            tenant_id=tenant_id,
            run_id=run_id,
            from_status=run["status"],
            to_status="NEEDS_ATTENTION",
            reason=reason,
            failure_class=failure_class,
            occurred_at=now,
        ))
    return task_ids


def pending_pause_stops(connection: Connection) -> list[tuple[str, str, str, int]]:
    return [
        (str(row["room_id"]), str(row["id"]), str(row["task_id"]), int(row["dispatch_epoch"]))
        for row in connection.execute(
            select(
                agentteams_task_delivery.c.id,
                agentteams_task_delivery.c.room_id,
                agentteams_task_delivery.c.task_id,
                agentteams_task_delivery.c.dispatch_epoch,
            ).where(
                agentteams_task_delivery.c.status == "PAUSE_STOP_PENDING"
            )
        ).mappings()
    ]


def mark_pause_stop_sent(connection: Connection, delivery_id: UUID | str, *, now: datetime) -> None:
    connection.execute(
        update(agentteams_task_delivery)
        .where(
            agentteams_task_delivery.c.id == UUID(str(delivery_id)),
            agentteams_task_delivery.c.status == "PAUSE_STOP_PENDING",
        )
        .values(status="PAUSED", completed_at=now)
    )


__all__ = [
    "AgentWorkerBusy",
    "assert_agent_worker_available",
    "complete_task_delivery",
    "drain_worker_lease_for_delivery",
    "fail_worker_lease",
    "mark_pause_stop_sent",
    "pending_pause_stops",
    "physical_worker_name",
    "prepare_worker_lease",
    "PreparedWorkerLease",
    "record_task_delivery",
    "reconcile_expired_task_deliveries",
    "reconcile_expired_undelivered_tasks",
    "task_usage_baseline",
]
