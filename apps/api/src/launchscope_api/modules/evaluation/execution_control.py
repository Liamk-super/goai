"""Durable Run-scoped pause, resume, and execution-admission boundaries."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    agentteams_task_delivery,
    budget_reservation,
    evaluation_run,
    evidence,
    model_invocation,
    outbox_message,
    physical_worker_execution_lease,
    run_control_request,
    run_execution_checkpoint,
    run_execution_control,
    run_execution_event,
    run_manifest,
    run_status_history,
    skill_invocation,
    task,
    tool_invocation,
    usage_record,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_api.modules.user_validation.application import IdempotencyConflictError
from launchscope_domain import FailureClass, RunStateMachine, RunStatus, TaskStatus
from launchscope_domain.services.run_state_machine import RunTransitionContext
from launchscope_domain.services.task_dag import TaskStateMachine
from launchscope_domain.value_objects import TenantScope

ACTIVE_INVOCATION_STATES = ("STARTED", "SUBMITTED")
TERMINAL_RUN_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"})


class RunControlConflictError(ValueError):
    """An execution-control command raced with a newer control epoch."""


class RunNotPausableError(ValueError):
    """The Run lifecycle or control state cannot be paused."""


class RunNotResumableError(ValueError):
    """The Run is not durably paused or has unresolved side effects."""


class RunNotRecoverableError(ValueError):
    """The Run is not eligible for the explicit Demo recovery command."""


class RunExecutionPausedError(PermissionError):
    """New paid or side-effecting work is prohibited for this Run."""


class ModelAdmissionRejected(RuntimeError):
    """A model request failed a local, known admission rule before upstream submission."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class ExecutionControlProjection:
    run_id: UUID
    state: str
    control_epoch: int
    usage_settlement_status: str
    in_flight_count: int
    pause_requested_at: datetime | None
    paused_at: datetime | None
    resumed_at: datetime | None
    last_error: str | None
    checkpoint: dict[str, object] | None
    remaining_budget: dict[str, object] | None
    usage_after_pause: dict[str, object]

    @property
    def resumable(self) -> bool:
        return self.state == "PAUSED"

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": str(self.run_id),
            "state": self.state,
            "control_epoch": self.control_epoch,
            "usage_settlement_status": self.usage_settlement_status,
            "in_flight_count": self.in_flight_count,
            "resumable": self.resumable,
            "pause_requested_at": _iso(self.pause_requested_at),
            "paused_at": _iso(self.paused_at),
            "resumed_at": _iso(self.resumed_at),
            "last_error": self.last_error,
            "checkpoint": self.checkpoint,
            "remaining_budget": self.remaining_budget,
            "usage_after_pause": self.usage_after_pause,
        }


@dataclass(frozen=True, slots=True)
class RunRecoveryProjection:
    run_id: UUID
    run_status: str
    execution_control: ExecutionControlProjection
    recovered_task_ids: tuple[UUID, ...]
    preserved_task_ids: tuple[UUID, ...]
    dispatched_task_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": str(self.run_id),
            "run_status": self.run_status,
            "execution_control": self.execution_control.to_dict(),
            "recovered_task_ids": [str(value) for value in self.recovered_task_ids],
            "preserved_task_ids": [str(value) for value in self.preserved_task_ids],
            "dispatched_task_count": self.dispatched_task_count,
        }


@dataclass(frozen=True, slots=True)
class ModelAdmission:
    invocation_id: UUID
    tenant_id: UUID
    run_id: UUID
    task_id: UUID
    delivery_id: UUID | None
    agent_code: str
    control_epoch: int
    dispatch_epoch: int | None
    invocation_seq: int | None


def pause_control_enabled() -> bool:
    return os.getenv("RUN_PAUSE_CONTROL_ENABLED", "true").strip().lower() in {"1", "true", "yes"}


def assert_run_active(session: Session, tenant_id: UUID, run_id: UUID, *, expected_epoch: int | None = None) -> int:
    row = session.execute(
        select(run_execution_control.c.state, run_execution_control.c.control_epoch).where(
            run_execution_control.c.tenant_id == tenant_id,
            run_execution_control.c.run_id == run_id,
        ).with_for_update()
    ).mappings().one_or_none()
    if row is None:
        raise RunExecutionPausedError("Run execution control is unavailable; new external work is denied")
    epoch = int(row["control_epoch"])
    if row["state"] != "ACTIVE" or (expected_epoch is not None and expected_epoch != epoch):
        raise RunExecutionPausedError("RUN_PAUSED: new model and tool work is denied")
    return epoch


class ExecutionControlApplication:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get(self, actor: Actor, run_id: UUID) -> ExecutionControlProjection:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            self._require_run(session, actor.tenant_id, run_id)
            return self._projection(session, actor.tenant_id, run_id)

    def pause(
        self,
        actor: Actor,
        run_id: UUID,
        *,
        expected_control_epoch: int,
        reason: str,
        idempotency_key: str,
        correlation_id: UUID,
    ) -> ExecutionControlProjection:
        if not pause_control_enabled():
            raise RunNotPausableError("Run pause control is disabled")
        if reason != "USER_EXIT":
            raise ValueError("pause reason must be USER_EXIT")
        request_hash = _request_hash("PAUSE", run_id, expected_control_epoch, reason)
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            replay = self._replay(session, actor.tenant_id, idempotency_key, request_hash)
            if replay is not None:
                return _projection_from_response(replay)
            run = self._require_run(session, actor.tenant_id, run_id, lock=True)
            control = self._control(session, actor.tenant_id, run_id, lock=True)
            if run["status"] in TERMINAL_RUN_STATES or control["state"] == "CLOSED":
                raise RunNotPausableError("RUN_NOT_PAUSABLE: terminal Run")
            if int(control["control_epoch"]) != expected_control_epoch:
                raise RunControlConflictError("RUN_CONTROL_CONFLICT: execution-control epoch is stale")
            if control["state"] == "PAUSED":
                projection = self._projection(session, actor.tenant_id, run_id)
                self._record_request(
                    session, actor, run_id, "PAUSE", idempotency_key, request_hash, correlation_id, projection
                )
                return projection
            if control["state"] in {"PAUSE_REQUESTED", "PAUSE_BLOCKED"}:
                raise RunControlConflictError("RUN_CONTROL_CONFLICT: pause is already being settled")

            new_epoch = expected_control_epoch + 1
            session.execute(
                update(physical_worker_execution_lease)
                .where(
                    physical_worker_execution_lease.c.tenant_id == actor.tenant_id,
                    physical_worker_execution_lease.c.run_id == run_id,
                    physical_worker_execution_lease.c.state.in_(("PREPARING", "ACTIVE")),
                )
                .values(state="DRAINING", draining_at=now, updated_at=now)
            )
            self._reject_unsubmitted_model_invocations(session, actor.tenant_id, run_id, now)
            active_count = self._active_external_work_count(session, actor.tenant_id, run_id)
            session.execute(
                update(run_execution_control)
                .where(
                    run_execution_control.c.tenant_id == actor.tenant_id,
                    run_execution_control.c.run_id == run_id,
                    run_execution_control.c.control_epoch == expected_control_epoch,
                    run_execution_control.c.state == "ACTIVE",
                )
                .values(
                    state="PAUSE_REQUESTED",
                    control_epoch=new_epoch,
                    requested_by=actor.actor_id,
                    pause_reason=reason,
                    usage_settlement_status="PENDING" if active_count else "SETTLED",
                    in_flight_count=active_count,
                    pause_requested_at=now,
                    paused_at=None,
                    last_error=None,
                    updated_at=now,
                )
            )
            self._hold_unsubmitted_task_events(session, actor.tenant_id, run_id)
            self._event(session, actor.tenant_id, run_id, "run.pause_requested", "PAUSE_REQUESTED", new_epoch, now)
            if active_count == 0:
                self._finalize_pause(session, actor.tenant_id, run_id, new_epoch, now)
            projection = self._projection(session, actor.tenant_id, run_id)
            self._record_request(
                session, actor, run_id, "PAUSE", idempotency_key, request_hash, correlation_id, projection
            )
            return projection

    def resume(
        self,
        actor: Actor,
        run_id: UUID,
        *,
        expected_control_epoch: int,
        idempotency_key: str,
        correlation_id: UUID,
    ) -> ExecutionControlProjection:
        request_hash = _request_hash("RESUME", run_id, expected_control_epoch, None)
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            replay = self._replay(session, actor.tenant_id, idempotency_key, request_hash)
            if replay is not None:
                return _projection_from_response(replay)
            self._require_run(session, actor.tenant_id, run_id, lock=True)
            control = self._control(session, actor.tenant_id, run_id, lock=True)
            if int(control["control_epoch"]) != expected_control_epoch:
                raise RunControlConflictError("RUN_CONTROL_CONFLICT: execution-control epoch is stale")
            if control["state"] != "PAUSED" or control["usage_settlement_status"] == "UNKNOWN":
                raise RunNotResumableError("RUN_NOT_RESUMABLE: Run is not safely paused")

            interrupted = session.execute(
                select(run_execution_checkpoint.c.interrupted_task_ids)
                .where(
                    run_execution_checkpoint.c.tenant_id == actor.tenant_id,
                    run_execution_checkpoint.c.run_id == run_id,
                    run_execution_checkpoint.c.control_epoch == expected_control_epoch,
                )
            ).scalar_one_or_none() or []
            interrupted_ids = [UUID(str(value)) for value in interrupted]
            new_epoch = expected_control_epoch + 1
            session.execute(
                update(run_execution_control)
                .where(
                    run_execution_control.c.tenant_id == actor.tenant_id,
                    run_execution_control.c.run_id == run_id,
                    run_execution_control.c.control_epoch == expected_control_epoch,
                    run_execution_control.c.state == "PAUSED",
                )
                .values(
                    state="ACTIVE",
                    control_epoch=new_epoch,
                    requested_by=actor.actor_id,
                    pause_reason=None,
                    usage_settlement_status="NONE",
                    in_flight_count=0,
                    resumed_at=now,
                    last_error=None,
                    updated_at=now,
                )
            )
            self._cancel_held_task_events(session, actor.tenant_id, run_id)
            if interrupted_ids:
                from .task_dispatch import enqueue_ready_tasks

                stage_codes = session.execute(
                    select(task.c.stage_code)
                    .where(
                        task.c.tenant_id == actor.tenant_id,
                        task.c.run_id == run_id,
                        task.c.id.in_(interrupted_ids),
                        task.c.status == "READY",
                    )
                    .distinct()
                ).scalars()
                for stage_code in stage_codes:
                    enqueue_ready_tasks(session, actor.tenant_id, run_id, str(stage_code))
            self._event(session, actor.tenant_id, run_id, "run.resumed", "ACTIVE", new_epoch, now)
            projection = self._projection(session, actor.tenant_id, run_id)
            self._record_request(
                session, actor, run_id, "RESUME", idempotency_key, request_hash, correlation_id, projection
            )
            return projection

    def recover(
        self,
        actor: Actor,
        run_id: UUID,
        *,
        expected_control_epoch: int,
        force: bool,
        idempotency_key: str,
        correlation_id: UUID,
    ) -> RunRecoveryProjection:
        if os.getenv("LAUNCHSCOPE_DEMO_MODE", "").strip().lower() != "true":
            raise NotFoundError("Demo run recovery is disabled")
        if not force:
            raise RunNotRecoverableError("RUN_NOT_RECOVERABLE: force must be true")
        request_hash = _request_hash("RECOVER", run_id, expected_control_epoch, "FORCE")
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            replay = self._replay(session, actor.tenant_id, idempotency_key, request_hash)
            if replay is not None:
                return _recovery_from_response(replay)
            run = self._require_run(session, actor.tenant_id, run_id, lock=True)
            control = self._control(session, actor.tenant_id, run_id, lock=True)
            if run["status"] != "NEEDS_ATTENTION" or control["state"] == "CLOSED":
                raise RunNotRecoverableError("RUN_NOT_RECOVERABLE: Run is not waiting for attention")
            if int(control["control_epoch"]) != expected_control_epoch:
                raise RunControlConflictError("RUN_CONTROL_CONFLICT: execution-control epoch is stale")

            failure_class = None
            try:
                if run["last_failure_class"]:
                    failure_class = FailureClass(str(run["last_failure_class"]))
            except ValueError:
                failure_class = None
            RunStateMachine.transition(
                RunStatus.NEEDS_ATTENTION,
                RunStatus.RUNNING,
                RunTransitionContext(
                    human_resume=True,
                    failure_class=failure_class,
                    demo_force_resume=True,
                ),
            )

            task_rows = session.execute(
                select(
                    task.c.id,
                    task.c.status,
                    task.c.stage_code,
                    task.c.required,
                    task.c.last_failure_class,
                )
                .where(task.c.tenant_id == actor.tenant_id, task.c.run_id == run_id)
                .order_by(task.c.created_at, task.c.id)
                .with_for_update()
            ).mappings().all()
            restartable_statuses = {"READY", "RUNNING", "NEEDS_ATTENTION", "KNOWN_FAILED"}
            recovered_rows = [
                row
                for row in task_rows
                if row["status"] in {"READY", "RUNNING", "NEEDS_ATTENTION"}
                or (
                    row["status"] == "KNOWN_FAILED"
                    and bool(row["required"])
                    and row["last_failure_class"] not in {"SUBMISSION_UNKNOWN", "BILLING_UNKNOWN"}
                )
            ]
            if not recovered_rows:
                raise RunNotRecoverableError("RUN_NOT_RECOVERABLE: Run has no executable unfinished Tasks")
            for row in recovered_rows:
                if row["status"] in {"RUNNING", "NEEDS_ATTENTION"}:
                    TaskStateMachine.transition(
                        TaskStatus(str(row["status"])),
                        TaskStatus.PENDING,
                        demo_force_resume=True,
                    )
                elif row["status"] == "KNOWN_FAILED":
                    TaskStateMachine.transition(
                        TaskStatus.FAILED,
                        TaskStatus.PENDING,
                        known_status=True,
                        retry_available=True,
                    )

            recovered_ids = tuple(UUID(str(row["id"])) for row in recovered_rows)
            preserved_ids = tuple(
                UUID(str(row["id"])) for row in task_rows if row not in recovered_rows
            )
            new_epoch = expected_control_epoch + 1
            session.execute(
                update(outbox_message)
                .where(
                    outbox_message.c.tenant_id == actor.tenant_id,
                    outbox_message.c.aggregate_id == run_id,
                    outbox_message.c.event_type == "evaluation.task.ready.v1",
                    outbox_message.c.publish_status.in_(("PENDING", "HELD")),
                )
                .values(publish_status="CANCELLED", last_error="superseded by Demo force recovery")
            )
            session.execute(
                update(physical_worker_execution_lease)
                .where(
                    physical_worker_execution_lease.c.tenant_id == actor.tenant_id,
                    physical_worker_execution_lease.c.run_id == run_id,
                    physical_worker_execution_lease.c.state.in_(("PREPARING", "ACTIVE", "DRAINING")),
                )
                .values(
                    state="RELEASED",
                    released_at=now,
                    last_error="superseded by Demo force recovery",
                    updated_at=now,
                )
            )
            session.execute(
                update(task)
                .where(
                    task.c.tenant_id == actor.tenant_id,
                    task.c.run_id == run_id,
                    task.c.id.in_(recovered_ids),
                    task.c.status.in_(tuple(restartable_statuses)),
                )
                .values(
                    status="READY",
                    lease_token=None,
                    dispatch_epoch=task.c.dispatch_epoch + 1,
                    last_failure_class=None,
                    last_error=None,
                    side_effect_started=False,
                    updated_at=now,
                )
            )
            from launchscope_api.modules.supervisor.material_routing import repair_recovered_task_material_routes

            repair_recovered_task_material_routes(
                session,
                actor.tenant_id,
                run_id,
                recovered_ids,
                now,
            )
            control_update: Any = session.execute(
                update(run_execution_control)
                .where(
                    run_execution_control.c.tenant_id == actor.tenant_id,
                    run_execution_control.c.run_id == run_id,
                    run_execution_control.c.control_epoch == expected_control_epoch,
                    run_execution_control.c.state != "CLOSED",
                )
                .values(
                    state="ACTIVE",
                    control_epoch=new_epoch,
                    requested_by=actor.actor_id,
                    pause_reason=None,
                    usage_settlement_status="NONE",
                    in_flight_count=0,
                    resumed_at=now,
                    last_error=None,
                    updated_at=now,
                )
            )
            if control_update.rowcount != 1:
                raise RunControlConflictError("RUN_CONTROL_CONFLICT: execution-control epoch changed")
            session.execute(
                update(evaluation_run)
                .where(
                    evaluation_run.c.tenant_id == actor.tenant_id,
                    evaluation_run.c.id == run_id,
                    evaluation_run.c.status == "NEEDS_ATTENTION",
                )
                .values(
                    status="RUNNING",
                    current_stage=(
                        str(recovered_rows[0]["stage_code"])
                        if run["current_stage"] == "NEEDS_ATTENTION"
                        else run["current_stage"]
                    ),
                    state_flags={**(run["state_flags"] or {}), "dispatch_pending": True},
                    last_failure_class=None,
                    attention_reason=None,
                    updated_at=now,
                )
            )
            session.execute(
                insert(run_status_history).values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    from_status="NEEDS_ATTENTION",
                    to_status="RUNNING",
                    reason="Demo force recovery requested by user",
                    failure_class=run["last_failure_class"],
                    occurred_at=now,
                )
            )
            self._event(
                session,
                actor.tenant_id,
                run_id,
                "run.resumed",
                "ACTIVE",
                new_epoch,
                now,
                reason=f"run.force_recovered: restarted {len(recovered_ids)} unfinished Tasks",
            )

            from .task_dispatch import enqueue_ready_tasks

            dispatched = 0
            for stage_code in dict.fromkeys(str(row["stage_code"]) for row in recovered_rows):
                dispatched += enqueue_ready_tasks(session, actor.tenant_id, run_id, stage_code)
            projection = RunRecoveryProjection(
                run_id=run_id,
                run_status="RUNNING",
                execution_control=self._projection(session, actor.tenant_id, run_id),
                recovered_task_ids=recovered_ids,
                preserved_task_ids=preserved_ids,
                dispatched_task_count=dispatched,
            )
            self._record_request(
                session,
                actor,
                run_id,
                "RESUME",
                idempotency_key,
                request_hash,
                correlation_id,
                projection,
            )
            return projection

    @staticmethod
    def settle_invocation(
        session: Session,
        invocation_id: UUID,
        *,
        status: str,
        upstream_request_id: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cost: Decimal | None = None,
        delivery_status: str | None = None,
        terminal_seen_at: datetime | None = None,
        usage_received_at: datetime | None = None,
        failure_class: str | None = None,
        error: str | None = None,
    ) -> None:
        if status not in {"SETTLED", "REJECTED", "SUBMISSION_UNKNOWN"}:
            raise ValueError("invocation settlement status is invalid")
        resolved_delivery_status = delivery_status or (
            "DELIVERED" if status in {"SETTLED", "REJECTED"} else "DELIVERY_UNKNOWN"
        )
        if resolved_delivery_status not in {"TERMINAL_SEEN", "DELIVERED", "DELIVERY_UNKNOWN"}:
            raise ValueError("invocation delivery settlement status is invalid")
        now = datetime.now(UTC)
        invocation = session.execute(
            select(model_invocation).where(model_invocation.c.id == invocation_id).with_for_update()
        ).mappings().one()
        if invocation["status"] not in ACTIVE_INVOCATION_STATES:
            return
        settled: Any = session.execute(
            update(model_invocation)
            .where(
                model_invocation.c.id == invocation_id,
                model_invocation.c.status.in_(ACTIVE_INVOCATION_STATES),
            )
            .values(
                status=status,
                delivery_status=resolved_delivery_status,
                upstream_request_id=upstream_request_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                terminal_seen_at=terminal_seen_at,
                usage_received_at=usage_received_at,
                settled_at=now,
                failure_class=failure_class,
                last_error=error,
            )
        )
        if settled.rowcount != 1:
            return
        if invocation.get("delivery_id") is not None:
            remaining_for_delivery = int(session.execute(
                select(func.count())
                .select_from(model_invocation)
                .where(
                    model_invocation.c.tenant_id == invocation["tenant_id"],
                    model_invocation.c.delivery_id == invocation["delivery_id"],
                    model_invocation.c.status.in_(ACTIVE_INVOCATION_STATES),
                )
            ).scalar_one())
            if remaining_for_delivery == 0:
                lease_state = session.execute(select(physical_worker_execution_lease.c.state).where(
                    physical_worker_execution_lease.c.tenant_id == invocation["tenant_id"],
                    physical_worker_execution_lease.c.delivery_id == invocation["delivery_id"],
                )).scalar_one_or_none()
                session.execute(
                    update(physical_worker_execution_lease)
                    .where(
                        physical_worker_execution_lease.c.tenant_id == invocation["tenant_id"],
                        physical_worker_execution_lease.c.delivery_id == invocation["delivery_id"],
                        physical_worker_execution_lease.c.state == "DRAINING",
                    )
                    .values(state="RELEASED", released_at=now, updated_at=now)
                )
                if lease_state == "DRAINING":
                    from .model_reconciliation import reconcile_gateway_delivery_usage

                    delivery = session.execute(select(
                        agentteams_task_delivery.c.dispatch_epoch,
                        agentteams_task_delivery.c.agent_code,
                    ).where(
                        agentteams_task_delivery.c.tenant_id == invocation["tenant_id"],
                        agentteams_task_delivery.c.id == invocation["delivery_id"],
                    )).mappings().one_or_none()
                    if delivery is not None:
                        reconciliation = reconcile_gateway_delivery_usage(
                            session,
                            tenant_id=UUID(str(invocation["tenant_id"])),
                            run_id=UUID(str(invocation["run_id"])),
                            task_id=UUID(str(invocation["task_id"])),
                            dispatch_epoch=int(delivery["dispatch_epoch"]),
                            agent_code=str(delivery["agent_code"]),
                            now=now,
                            usage_reader=None,
                        )
                        if reconciliation.failure_class is not None:
                            failure_class = reconciliation.failure_class
                            error = reconciliation.reason or error
        control = session.execute(
            select(run_execution_control)
            .where(
                run_execution_control.c.tenant_id == invocation["tenant_id"],
                run_execution_control.c.run_id == invocation["run_id"],
            )
            .with_for_update()
        ).mappings().one()
        uncertainty = (
            status == "SUBMISSION_UNKNOWN"
            or resolved_delivery_status == "DELIVERY_UNKNOWN"
            or failure_class is not None
        )
        if uncertainty:
            settlement_status = "UNKNOWN" if status == "SUBMISSION_UNKNOWN" else "SETTLED"
            resolved_failure = failure_class or (
                "SUBMISSION_UNKNOWN" if status == "SUBMISSION_UNKNOWN" else "MODEL_DELIVERY_UNKNOWN"
            )
            ExecutionControlApplication._block_pause(
                session,
                invocation["tenant_id"],
                invocation["run_id"],
                int(control["control_epoch"]),
                now,
                error or "Model submission, usage, or downstream delivery is unknown",
                failure_class=resolved_failure,
                settlement_status=settlement_status,
            )
            return
        if control["state"] != "PAUSE_REQUESTED":
            return
        remaining = ExecutionControlApplication._active_external_work_count(
            session, invocation["tenant_id"], invocation["run_id"]
        )
        if remaining == 0:
            ExecutionControlApplication._finalize_pause(
                session,
                invocation["tenant_id"],
                invocation["run_id"],
                int(control["control_epoch"]),
                now,
            )
        else:
            session.execute(
                update(run_execution_control)
                .where(run_execution_control.c.id == control["id"])
                .values(in_flight_count=remaining, updated_at=now)
            )

    @staticmethod
    def mark_invocation_delivery(
        session: Session,
        invocation_id: UUID,
        *,
        delivery_status: str,
        error: str | None = None,
    ) -> None:
        if delivery_status not in {"DELIVERED", "DELIVERY_UNKNOWN"}:
            raise ValueError("terminal invocation delivery status is invalid")
        now = datetime.now(UTC)
        invocation = session.execute(
            select(model_invocation).where(model_invocation.c.id == invocation_id).with_for_update()
        ).mappings().one()
        if invocation["status"] not in {"SETTLED", "REJECTED"}:
            return
        if invocation["delivery_status"] == "DELIVERED":
            return
        if invocation["delivery_status"] != "TERMINAL_SEEN":
            if delivery_status == "DELIVERY_UNKNOWN" and invocation["delivery_status"] == "DELIVERY_UNKNOWN":
                return
            raise ValueError("invocation delivery can only finalize after terminal observation")
        session.execute(
            update(model_invocation)
            .where(
                model_invocation.c.id == invocation_id,
                model_invocation.c.delivery_status == "TERMINAL_SEEN",
            )
            .values(
                delivery_status=delivery_status,
                failure_class="MODEL_DELIVERY_UNKNOWN" if delivery_status == "DELIVERY_UNKNOWN" else None,
                last_error=error if delivery_status == "DELIVERY_UNKNOWN" else invocation["last_error"],
            )
        )
        if delivery_status == "DELIVERY_UNKNOWN":
            control = session.execute(
                select(run_execution_control)
                .where(
                    run_execution_control.c.tenant_id == invocation["tenant_id"],
                    run_execution_control.c.run_id == invocation["run_id"],
                )
                .with_for_update()
            ).mappings().one()
            ExecutionControlApplication._block_pause(
                session,
                invocation["tenant_id"],
                invocation["run_id"],
                int(control["control_epoch"]),
                now,
                error or "Model terminal event was observed but downstream delivery is unknown",
                failure_class="MODEL_DELIVERY_UNKNOWN",
                settlement_status="SETTLED",
            )

    @staticmethod
    def _finalize_pause(session: Session, tenant_id: UUID, run_id: UUID, epoch: int, now: datetime) -> None:
        running = list(
            session.execute(
                select(task.c.id).where(
                    task.c.tenant_id == tenant_id,
                    task.c.run_id == run_id,
                    task.c.status == "RUNNING",
                )
            ).scalars()
        )
        resumable_tasks = list(
            session.execute(
                select(task.c.id).where(
                    task.c.tenant_id == tenant_id,
                    task.c.run_id == run_id,
                    task.c.status.in_(("READY", "RUNNING")),
                )
            ).scalars()
        )
        completed = list(
            session.execute(
                select(task.c.id).where(
                    task.c.tenant_id == tenant_id,
                    task.c.run_id == run_id,
                    task.c.status.in_(("SUCCEEDED", "KNOWN_FAILED", "FAILED")),
                )
            ).scalars()
        )
        evidence_ids = list(
            session.execute(
                select(evidence.c.id).where(evidence.c.tenant_id == tenant_id, evidence.c.run_id == run_id)
            ).scalars()
        )
        usage = session.execute(
            select(func.coalesce(func.sum(usage_record.c.quantity), 0), func.coalesce(func.sum(usage_record.c.cost), 0))
            .where(usage_record.c.tenant_id == tenant_id, usage_record.c.run_id == run_id)
        ).one()
        existing = session.execute(
            select(run_execution_checkpoint.c.id).where(
                run_execution_checkpoint.c.tenant_id == tenant_id,
                run_execution_checkpoint.c.run_id == run_id,
                run_execution_checkpoint.c.control_epoch == epoch,
            )
        ).scalar_one_or_none()
        if existing is None:
            session.execute(
                insert(run_execution_checkpoint).values(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    run_id=run_id,
                    control_epoch=epoch,
                    interrupted_task_ids=[str(value) for value in resumable_tasks],
                    completed_task_ids=[str(value) for value in completed],
                    evidence_ids=[str(value) for value in evidence_ids],
                    usage_summary={"quantity": str(usage[0]), "cost": str(usage[1])},
                    created_at=now,
                )
            )
        if resumable_tasks:
            session.execute(
                update(task)
                .where(
                    task.c.tenant_id == tenant_id,
                    task.c.run_id == run_id,
                    task.c.id.in_(resumable_tasks),
                    task.c.status.in_(("READY", "RUNNING")),
                )
                .values(
                    status="READY",
                    lease_token=None,
                    dispatch_epoch=task.c.dispatch_epoch + 1,
                    updated_at=now,
                )
            )
            session.execute(
                update(agentteams_task_delivery)
                .where(
                    agentteams_task_delivery.c.tenant_id == tenant_id,
                    agentteams_task_delivery.c.run_id == run_id,
                    agentteams_task_delivery.c.task_id.in_(running),
                    agentteams_task_delivery.c.status == "DELIVERED",
                )
                .values(status="PAUSE_STOP_PENDING")
            )
        draining_leases = session.execute(select(
            physical_worker_execution_lease.c.id,
            physical_worker_execution_lease.c.delivery_id,
        ).where(
            physical_worker_execution_lease.c.tenant_id == tenant_id,
            physical_worker_execution_lease.c.run_id == run_id,
            physical_worker_execution_lease.c.state == "DRAINING",
        ).with_for_update()).mappings().all()
        for lease in draining_leases:
            active = int(session.execute(
                select(func.count()).select_from(model_invocation).where(
                    model_invocation.c.tenant_id == tenant_id,
                    model_invocation.c.delivery_id == lease["delivery_id"],
                    model_invocation.c.status.in_(ACTIVE_INVOCATION_STATES),
                )
            ).scalar_one())
            if active == 0:
                session.execute(update(physical_worker_execution_lease).where(
                    physical_worker_execution_lease.c.id == lease["id"],
                    physical_worker_execution_lease.c.state == "DRAINING",
                ).values(state="RELEASED", released_at=now, updated_at=now))
        session.execute(
            update(run_execution_control)
            .where(
                run_execution_control.c.tenant_id == tenant_id,
                run_execution_control.c.run_id == run_id,
                run_execution_control.c.control_epoch == epoch,
                run_execution_control.c.state == "PAUSE_REQUESTED",
            )
            .values(
                state="PAUSED",
                usage_settlement_status="SETTLED",
                in_flight_count=0,
                paused_at=now,
                updated_at=now,
            )
        )
        ExecutionControlApplication._event(session, tenant_id, run_id, "run.paused", "PAUSED", epoch, now)

    @staticmethod
    def _block_pause(
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        epoch: int,
        now: datetime,
        reason: str,
        *,
        failure_class: str = "SUBMISSION_UNKNOWN",
        settlement_status: str = "UNKNOWN",
    ) -> None:
        session.execute(
            update(run_execution_control)
            .where(run_execution_control.c.tenant_id == tenant_id, run_execution_control.c.run_id == run_id)
            .values(
                state="PAUSE_BLOCKED",
                usage_settlement_status=settlement_status,
                last_error=reason[:1000],
                updated_at=now,
            )
        )
        session.execute(
            update(evaluation_run)
            .where(evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id)
            .values(
                status="NEEDS_ATTENTION",
                last_failure_class=failure_class,
                attention_reason=reason[:1000],
                updated_at=now,
            )
        )
        ExecutionControlApplication._event(
            session, tenant_id, run_id, "run.pause_blocked", "PAUSE_BLOCKED", epoch, now, reason=reason
        )

    @staticmethod
    def _event(
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        event_type: str,
        state: str,
        epoch: int,
        now: datetime,
        *,
        reason: str | None = None,
    ) -> None:
        session.execute(
            insert(run_execution_event).values(
                id=uuid4(),
                tenant_id=tenant_id,
                run_id=run_id,
                event_type=event_type,
                control_state=state,
                control_epoch=epoch,
                data={"reason": reason} if reason else {},
                occurred_at=now,
            )
        )

    @staticmethod
    def _hold_unsubmitted_task_events(session: Session, tenant_id: UUID, run_id: UUID) -> None:
        session.execute(
            update(outbox_message)
            .where(
                outbox_message.c.tenant_id == tenant_id,
                outbox_message.c.aggregate_id == run_id,
                outbox_message.c.event_type == "evaluation.task.ready.v1",
                outbox_message.c.publish_status == "PENDING",
                outbox_message.c.attempts == 0,
                outbox_message.c.claimed_by.is_(None),
                outbox_message.c.claimed_at.is_(None),
            )
            .values(publish_status="HELD", last_error="held by durable Run pause")
        )

    @staticmethod
    def _reject_unsubmitted_model_invocations(
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        now: datetime,
    ) -> None:
        session.execute(
            update(model_invocation)
            .where(
                model_invocation.c.tenant_id == tenant_id,
                model_invocation.c.run_id == run_id,
                model_invocation.c.status == "STARTED",
            )
            .values(
                status="REJECTED",
                delivery_status="DELIVERED",
                prompt_tokens=0,
                completion_tokens=0,
                cost=Decimal("0"),
                settled_at=now,
                failure_class="PAUSED_BEFORE_SUBMISSION",
                last_error="pause committed before model submission admission",
            )
        )

    @staticmethod
    def _cancel_held_task_events(session: Session, tenant_id: UUID, run_id: UUID) -> None:
        session.execute(
            update(outbox_message)
            .where(
                outbox_message.c.tenant_id == tenant_id,
                outbox_message.c.aggregate_id == run_id,
                outbox_message.c.event_type == "evaluation.task.ready.v1",
                outbox_message.c.publish_status == "HELD",
            )
            .values(publish_status="CANCELLED", last_error="superseded by resumed dispatch epoch")
        )

    @staticmethod
    def settle_tool_invocation(
        session: Session,
        *,
        tenant_id: UUID,
        run_id: UUID,
        invocation_id: UUID,
        status: str,
        error: str | None = None,
    ) -> None:
        if status not in {"SUCCEEDED", "FAILED", "SUBMISSION_UNKNOWN"}:
            raise ValueError("tool settlement status is invalid")
        now = datetime.now(UTC)
        session.execute(
            update(tool_invocation)
            .where(
                tool_invocation.c.tenant_id == tenant_id,
                tool_invocation.c.id == invocation_id,
                tool_invocation.c.status == "STARTED",
            )
            .values(status=status)
        )
        control = session.execute(
            select(run_execution_control)
            .where(run_execution_control.c.tenant_id == tenant_id, run_execution_control.c.run_id == run_id)
            .with_for_update()
        ).mappings().one()
        if control["state"] != "PAUSE_REQUESTED":
            return
        if status == "SUBMISSION_UNKNOWN":
            ExecutionControlApplication._block_pause(
                session,
                tenant_id,
                run_id,
                int(control["control_epoch"]),
                now,
                error or "Tool submission, side effect, or billing state is unknown",
            )
            return
        remaining = ExecutionControlApplication._active_external_work_count(session, tenant_id, run_id)
        if remaining == 0:
            ExecutionControlApplication._finalize_pause(
                session, tenant_id, run_id, int(control["control_epoch"]), now
            )
        else:
            session.execute(
                update(run_execution_control)
                .where(run_execution_control.c.id == control["id"])
                .values(in_flight_count=remaining, updated_at=now)
            )

    @staticmethod
    def _active_external_work_count(session: Session, tenant_id: UUID, run_id: UUID) -> int:
        models = int(
            session.execute(
                select(func.count())
                .select_from(model_invocation)
                .where(
                    model_invocation.c.tenant_id == tenant_id,
                    model_invocation.c.run_id == run_id,
                    model_invocation.c.status.in_(ACTIVE_INVOCATION_STATES),
                )
            ).scalar_one()
        )
        tools = int(
            session.execute(
                select(func.count())
                .select_from(
                    tool_invocation.join(
                        skill_invocation,
                        (skill_invocation.c.tenant_id == tool_invocation.c.tenant_id)
                        & (skill_invocation.c.id == tool_invocation.c.skill_invocation_id),
                    ).join(
                        task,
                        (task.c.tenant_id == skill_invocation.c.tenant_id)
                        & (task.c.id == skill_invocation.c.task_id),
                    )
                )
                .where(
                    tool_invocation.c.tenant_id == tenant_id,
                    task.c.run_id == run_id,
                    tool_invocation.c.status == "STARTED",
                )
            ).scalar_one()
        )
        return models + tools

    @staticmethod
    def _require_run(session: Session, tenant_id: UUID, run_id: UUID, *, lock: bool = False) -> Any:
        statement = select(evaluation_run).where(
            evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id
        )
        if lock:
            statement = statement.with_for_update()
        row = session.execute(statement).mappings().one_or_none()
        if row is None:
            raise NotFoundError("run was not found")
        return row

    @staticmethod
    def _control(session: Session, tenant_id: UUID, run_id: UUID, *, lock: bool = False) -> Any:
        statement = select(run_execution_control).where(
            run_execution_control.c.tenant_id == tenant_id, run_execution_control.c.run_id == run_id
        )
        if lock:
            statement = statement.with_for_update()
        row = session.execute(statement).mappings().one_or_none()
        if row is None:
            raise RuntimeError("Run execution-control row is missing; apply migration 0026")
        return row

    @staticmethod
    def _projection(session: Session, tenant_id: UUID, run_id: UUID) -> ExecutionControlProjection:
        row = ExecutionControlApplication._control(session, tenant_id, run_id)
        checkpoint = session.execute(
            select(run_execution_checkpoint)
            .where(
                run_execution_checkpoint.c.tenant_id == tenant_id,
                run_execution_checkpoint.c.run_id == run_id,
            )
            .order_by(run_execution_checkpoint.c.control_epoch.desc())
            .limit(1)
        ).mappings().one_or_none()
        checkpoint_value: dict[str, object] | None = None
        if checkpoint is not None:
            checkpoint_value = {
                "interrupted_task_ids": list(checkpoint["interrupted_task_ids"] or []),
                "completed_task_ids": list(checkpoint["completed_task_ids"] or []),
                "evidence_ids": list(checkpoint["evidence_ids"] or []),
                "usage_summary": dict(checkpoint["usage_summary"] or {}),
            }
        reservation = session.execute(
            select(budget_reservation).where(
                budget_reservation.c.tenant_id == tenant_id,
                budget_reservation.c.run_id == run_id,
                budget_reservation.c.category == "run_total",
            )
        ).mappings().one_or_none()
        remaining_budget: dict[str, object] | None = None
        if reservation is not None:
            limit_amount = Decimal(reservation["limit_amount"])
            consumed_amount = Decimal(reservation["consumed_amount"])
            remaining_budget = {
                "currency": str(reservation["currency"]),
                "limit": str(limit_amount),
                "reserved": str(reservation["reserved_amount"]),
                "consumed": str(consumed_amount),
                "remaining": str(max(limit_amount - consumed_amount, Decimal(0))),
                "status": str(reservation["status"]),
            }
        usage_after_pause = {"tokens": 0, "cost": "0"}
        if row["pause_requested_at"] is not None:
            post_pause_usage = session.execute(
                select(
                    func.coalesce(
                        func.sum(
                            func.coalesce(model_invocation.c.prompt_tokens, 0)
                            + func.coalesce(model_invocation.c.completion_tokens, 0)
                        ),
                        0,
                    ),
                    func.coalesce(func.sum(model_invocation.c.cost), 0),
                ).where(
                    model_invocation.c.tenant_id == tenant_id,
                    model_invocation.c.run_id == run_id,
                    model_invocation.c.settled_at >= row["pause_requested_at"],
                )
            ).one()
            usage_after_pause = {"tokens": int(post_pause_usage[0]), "cost": str(post_pause_usage[1])}
        return ExecutionControlProjection(
            run_id=run_id,
            state=str(row["state"]),
            control_epoch=int(row["control_epoch"]),
            usage_settlement_status=str(row["usage_settlement_status"]),
            in_flight_count=int(row["in_flight_count"]),
            pause_requested_at=row["pause_requested_at"],
            paused_at=row["paused_at"],
            resumed_at=row["resumed_at"],
            last_error=row["last_error"],
            checkpoint=checkpoint_value,
            remaining_budget=remaining_budget,
            usage_after_pause=usage_after_pause,
        )

    @staticmethod
    def _replay(session: Session, tenant_id: UUID, key: str, request_hash: str) -> dict[str, object] | None:
        row = session.execute(
            select(run_control_request.c.request_sha256, run_control_request.c.response).where(
                run_control_request.c.tenant_id == tenant_id,
                run_control_request.c.idempotency_key == key,
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        if row["request_sha256"] != request_hash:
            raise IdempotencyConflictError("IDEMPOTENCY_CONFLICT")
        return dict(row["response"])

    @staticmethod
    def _record_request(
        session: Session,
        actor: Actor,
        run_id: UUID,
        operation: str,
        key: str,
        request_hash: str,
        correlation_id: UUID,
        projection: ExecutionControlProjection | RunRecoveryProjection,
    ) -> None:
        session.execute(
            insert(run_control_request).values(
                id=uuid4(),
                tenant_id=actor.tenant_id,
                run_id=run_id,
                operation=operation,
                idempotency_key=key,
                request_sha256=request_hash,
                response=projection.to_dict(),
                correlation_id=correlation_id,
                created_at=datetime.now(UTC),
            )
        )


def admit_model_invocation(
    session: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    task_id: UUID,
    agent_code: str,
    expected_epoch: int,
    model: str,
    request_sha256: str,
    delivery_id: UUID | None = None,
    dispatch_epoch: int | None = None,
    projected_input_tokens: int = 0,
    projected_output_tokens: int = 0,
    budget_hold_amount: Decimal = Decimal("0"),
) -> ModelAdmission:
    control = session.execute(
        select(run_execution_control)
        .where(run_execution_control.c.tenant_id == tenant_id, run_execution_control.c.run_id == run_id)
        .with_for_update()
    ).mappings().one_or_none()
    if control is None or control["state"] != "ACTIVE" or int(control["control_epoch"]) != expected_epoch:
        raise RunExecutionPausedError("RUN_PAUSED: model request was rejected before upstream submission")
    active_task = session.execute(
        select(task.c.agent_identity_ref).where(
            task.c.tenant_id == tenant_id,
            task.c.run_id == run_id,
            task.c.id == task_id,
            task.c.status == "RUNNING",
        )
    ).scalar_one_or_none()
    if active_task is None or str(active_task).split("@", 1)[0] != agent_code:
        raise RunExecutionPausedError("model request is not bound to the active Agent task")
    invocation_seq = None
    if delivery_id is not None:
        delivery = session.execute(
            select(agentteams_task_delivery)
            .where(
                agentteams_task_delivery.c.tenant_id == tenant_id,
                agentteams_task_delivery.c.id == delivery_id,
                agentteams_task_delivery.c.run_id == run_id,
                agentteams_task_delivery.c.task_id == task_id,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if (
            delivery is None
            or delivery["status"] != "DELIVERED"
            or int(delivery["dispatch_epoch"]) != dispatch_epoch
            or delivery["agent_code"] != agent_code
            or delivery["accounting_mode"] != "GATEWAY_DELIVERY"
        ):
            raise ModelAdmissionRejected("DELIVERY_INACTIVE", "model request is not bound to an active delivery")
        lease = session.execute(
            select(physical_worker_execution_lease)
            .where(
                physical_worker_execution_lease.c.tenant_id == tenant_id,
                physical_worker_execution_lease.c.delivery_id == delivery_id,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if (
            lease is None
            or lease["state"] != "ACTIVE"
            or int(lease["control_epoch"]) != expected_epoch
            or int(lease["dispatch_epoch"]) != dispatch_epoch
        ):
            raise ModelAdmissionRejected("WORKER_LEASE_INACTIVE", "delivery capability is no longer active")
        unresolved_fingerprint = session.execute(
            select(model_invocation.c.id).where(
                model_invocation.c.tenant_id == tenant_id,
                model_invocation.c.delivery_id == delivery_id,
                model_invocation.c.request_sha256 == request_sha256,
                (
                    model_invocation.c.status.in_(ACTIVE_INVOCATION_STATES + ("SUBMISSION_UNKNOWN",))
                    | (model_invocation.c.delivery_status == "DELIVERY_UNKNOWN")
                ),
            ).limit(1)
        ).scalar_one_or_none()
        if unresolved_fingerprint is not None:
            raise ModelAdmissionRejected(
                "DUPLICATE_OR_UNRESOLVED_MODEL_REQUEST",
                "an equivalent request is already active or requires reconciliation",
            )
        active_delivery_call = session.execute(
            select(model_invocation.c.id).where(
                model_invocation.c.tenant_id == tenant_id,
                model_invocation.c.delivery_id == delivery_id,
                model_invocation.c.status.in_(ACTIVE_INVOCATION_STATES),
            ).limit(1)
        ).scalar_one_or_none()
        if active_delivery_call is not None:
            raise ModelAdmissionRejected("DELIVERY_CALL_IN_FLIGHT", "delivery already has an in-flight model call")
        prior_delivery_calls = int(session.execute(
            select(func.count()).select_from(model_invocation).where(
                model_invocation.c.tenant_id == tenant_id,
                model_invocation.c.delivery_id == delivery_id,
                model_invocation.c.status.in_(("STARTED", "SUBMITTED", "SETTLED", "SUBMISSION_UNKNOWN")),
            )
        ).scalar_one())
        if prior_delivery_calls >= int(delivery["max_model_calls"]):
            raise ModelAdmissionRejected("DELIVERY_MODEL_CALL_LIMIT", "delivery model call limit reached")
        manifest = session.execute(select(run_manifest.c.frozen_config).where(
            run_manifest.c.tenant_id == tenant_id,
            run_manifest.c.run_id == run_id,
        )).scalar_one()
        from .limit_amendment_application import effective_run_limits

        limits = effective_run_limits(
            session,
            tenant_id,
            run_id,
            manifest_limits=manifest.get("limits", {}),
        )
        allowed_models = set(manifest.get("model_runtime", {}).get("allowed_model_ids", []))
        if not allowed_models or model not in allowed_models:
            raise ModelAdmissionRejected("MODEL_NOT_ALLOWED", "model is outside the Run's frozen allowlist")
        pricing = manifest.get("model_pricing", {})
        if str(pricing.get("cost_mode") or "TOKEN_ONLY").upper() == "EXACT":
            try:
                budget_hold_amount = (
                    Decimal(projected_input_tokens) * Decimal(str(pricing["input_usd_per_million_tokens"]))
                    + Decimal(projected_output_tokens) * Decimal(str(pricing["output_usd_per_million_tokens"]))
                ) / Decimal(1_000_000)
            except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
                raise ModelAdmissionRejected(
                    "BILLING_POLICY_INVALID",
                    "Run frozen model pricing is incomplete",
                ) from exc
        else:
            budget_hold_amount = Decimal("0")
        run_calls = int(session.execute(
            select(func.count()).select_from(model_invocation).where(
                model_invocation.c.tenant_id == tenant_id,
                model_invocation.c.run_id == run_id,
                model_invocation.c.status.in_(("STARTED", "SUBMITTED", "SETTLED", "SUBMISSION_UNKNOWN")),
            )
        ).scalar_one())
        if run_calls >= int(limits.get("model_calls", 0)):
            raise ModelAdmissionRejected("RUN_MODEL_CALL_LIMIT", "Run model call limit reached")
        prompt_used, completion_used = session.execute(select(
            func.coalesce(func.sum(model_invocation.c.prompt_tokens), 0),
            func.coalesce(func.sum(model_invocation.c.completion_tokens), 0),
        ).where(
            model_invocation.c.tenant_id == tenant_id,
            model_invocation.c.run_id == run_id,
            model_invocation.c.status == "SETTLED",
        )).one()
        if int(prompt_used) + projected_input_tokens > int(limits.get("input_tokens", 0)):
            raise ModelAdmissionRejected("RUN_INPUT_TOKEN_LIMIT", "Run input Token limit would be exceeded")
        if int(completion_used) + projected_output_tokens > int(limits.get("output_tokens", 0)):
            raise ModelAdmissionRejected("RUN_OUTPUT_TOKEN_LIMIT", "Run output Token limit would be exceeded")
        if budget_hold_amount > 0:
            reservation = session.execute(select(budget_reservation).where(
                budget_reservation.c.tenant_id == tenant_id,
                budget_reservation.c.run_id == run_id,
                budget_reservation.c.category == "run_total",
            ).with_for_update()).mappings().one_or_none()
            open_holds = session.execute(select(
                func.coalesce(func.sum(model_invocation.c.budget_held_amount), 0)
            ).where(
                model_invocation.c.tenant_id == tenant_id,
                model_invocation.c.run_id == run_id,
                model_invocation.c.status.in_(ACTIVE_INVOCATION_STATES),
            )).scalar_one()
            projected_consumption = (
                reservation["consumed_amount"] + open_holds + budget_hold_amount
                if reservation is not None
                else None
            )
            if reservation is None or projected_consumption > reservation["limit_amount"]:
                raise ModelAdmissionRejected("RUN_BUDGET_LIMIT", "Run budget would be exceeded")
        invocation_seq = prior_delivery_calls + 1
    invocation_id = uuid4()
    session.execute(
        insert(model_invocation).values(
            id=invocation_id,
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=task_id,
            delivery_id=delivery_id,
            agent_code=agent_code,
            control_epoch=expected_epoch,
            dispatch_epoch=dispatch_epoch,
            invocation_seq=invocation_seq,
            model=model,
            status="STARTED",
            delivery_status="NOT_STARTED",
            request_sha256=request_sha256,
            budget_held_amount=budget_hold_amount,
            started_at=datetime.now(UTC),
        )
    )
    return ModelAdmission(
        invocation_id,
        tenant_id,
        run_id,
        task_id,
        delivery_id,
        agent_code,
        expected_epoch,
        dispatch_epoch,
        invocation_seq,
    )


def mark_model_invocation_submitted(
    session: Session,
    invocation_id: UUID,
    *,
    streaming: bool = False,
) -> ModelAdmission:
    identity = session.execute(select(
        model_invocation.c.tenant_id,
        model_invocation.c.run_id,
    ).where(model_invocation.c.id == invocation_id)).mappings().one_or_none()
    if identity is None:
        raise RunExecutionPausedError("model invocation is unavailable before upstream submission")
    control = session.execute(select(run_execution_control).where(
        run_execution_control.c.tenant_id == identity["tenant_id"],
        run_execution_control.c.run_id == identity["run_id"],
    ).with_for_update()).mappings().one()
    invocation = session.execute(select(model_invocation).where(
        model_invocation.c.id == invocation_id,
        model_invocation.c.tenant_id == identity["tenant_id"],
    ).with_for_update()).mappings().one()
    if (
        control["state"] != "ACTIVE"
        or int(control["control_epoch"]) != int(invocation["control_epoch"])
        or invocation["status"] != "STARTED"
    ):
        raise RunExecutionPausedError("RUN_PAUSED: model request was rejected before upstream submission")
    if invocation["delivery_id"] is not None:
        lease = session.execute(select(physical_worker_execution_lease).where(
            physical_worker_execution_lease.c.tenant_id == invocation["tenant_id"],
            physical_worker_execution_lease.c.delivery_id == invocation["delivery_id"],
        ).with_for_update()).mappings().one_or_none()
        if (
            lease is None
            or lease["state"] != "ACTIVE"
            or int(lease["control_epoch"]) != int(invocation["control_epoch"])
            or int(lease["dispatch_epoch"]) != int(invocation["dispatch_epoch"])
        ):
            raise RunExecutionPausedError("RUN_PAUSED: delivery lease closed before upstream submission")
    session.execute(update(model_invocation).where(
        model_invocation.c.id == invocation_id,
        model_invocation.c.status == "STARTED",
    ).values(
        status="SUBMITTED",
        delivery_status="STREAMING" if streaming else "NOT_STARTED",
        submitted_at=datetime.now(UTC),
    ))
    return ModelAdmission(
        UUID(str(invocation["id"])),
        UUID(str(invocation["tenant_id"])),
        UUID(str(invocation["run_id"])),
        UUID(str(invocation["task_id"])),
        UUID(str(invocation["delivery_id"])) if invocation["delivery_id"] is not None else None,
        str(invocation["agent_code"]),
        int(invocation["control_epoch"]),
        int(invocation["dispatch_epoch"]) if invocation["dispatch_epoch"] is not None else None,
        int(invocation["invocation_seq"]) if invocation["invocation_seq"] is not None else None,
    )


def _request_hash(operation: str, run_id: UUID, epoch: int, reason: str | None) -> str:
    body = json.dumps(
        {"operation": operation, "run_id": str(run_id), "expected_control_epoch": epoch, "reason": reason},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _projection_from_response(value: dict[str, object]) -> ExecutionControlProjection:
    checkpoint = value.get("checkpoint")
    return ExecutionControlProjection(
        run_id=UUID(str(value["run_id"])),
        state=str(value["state"]),
        control_epoch=int(str(value["control_epoch"])),
        usage_settlement_status=str(value["usage_settlement_status"]),
        in_flight_count=int(str(value["in_flight_count"])),
        pause_requested_at=_parse_datetime(value.get("pause_requested_at")),
        paused_at=_parse_datetime(value.get("paused_at")),
        resumed_at=_parse_datetime(value.get("resumed_at")),
        last_error=str(value["last_error"]) if value.get("last_error") else None,
        checkpoint=cast(dict[str, object], checkpoint) if isinstance(checkpoint, dict) else None,
        remaining_budget=(
            cast(dict[str, object], value["remaining_budget"])
            if isinstance(value.get("remaining_budget"), dict)
            else None
        ),
        usage_after_pause=(
            cast(dict[str, object], value["usage_after_pause"])
            if isinstance(value.get("usage_after_pause"), dict)
            else {"tokens": 0, "cost": "0"}
        ),
    )


def _recovery_from_response(value: dict[str, object]) -> RunRecoveryProjection:
    execution_control = value.get("execution_control")
    if not isinstance(execution_control, dict):
        raise RuntimeError("Stored recovery response is malformed")
    recovered_task_ids = value.get("recovered_task_ids", [])
    preserved_task_ids = value.get("preserved_task_ids", [])
    dispatched_task_count = value.get("dispatched_task_count", 0)
    if not isinstance(recovered_task_ids, list) or not isinstance(preserved_task_ids, list):
        raise RuntimeError("Stored recovery response task identifiers are malformed")
    if not isinstance(dispatched_task_count, int):
        raise RuntimeError("Stored recovery response dispatch count is malformed")
    return RunRecoveryProjection(
        run_id=UUID(str(value["run_id"])),
        run_status=str(value["run_status"]),
        execution_control=_projection_from_response(execution_control),
        recovered_task_ids=tuple(UUID(str(item)) for item in recovered_task_ids),
        preserved_task_ids=tuple(UUID(str(item)) for item in preserved_task_ids),
        dispatched_task_count=dispatched_task_count,
    )


def _parse_datetime(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


__all__ = [
    "ExecutionControlApplication",
    "ExecutionControlProjection",
    "ModelAdmission",
    "RunControlConflictError",
    "RunExecutionPausedError",
    "RunNotPausableError",
    "RunNotRecoverableError",
    "RunNotResumableError",
    "admit_model_invocation",
    "assert_run_active",
    "mark_model_invocation_submitted",
    "pause_control_enabled",
]
