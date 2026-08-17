"""Append-only Demo authorization for higher effective Run model limits."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    audit_event,
    evaluation_run,
    matrix_event_receipt,
    model_invocation,
    run_execution_control,
    run_limit_amendment,
    run_manifest,
    run_status_history,
    task,
    usage_record,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_domain.value_objects import TenantScope

DEMO_LIMIT_CEILINGS = {
    "model_calls": 4096,
    "input_tokens": 200_000_000,
    "output_tokens": 20_000_000,
}


class RunLimitAmendmentError(RuntimeError):
    pass


class RunLimitAmendmentConflict(RunLimitAmendmentError):
    pass


@dataclass(frozen=True, slots=True)
class RunLimitAmendmentProjection:
    amendment_id: UUID
    run_id: UUID
    task_id: UUID
    amendment_version: int
    control_epoch: int
    dispatch_epoch: int
    matrix_event_id: str
    effective_limits: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "amendment_id": str(self.amendment_id),
            "run_id": str(self.run_id),
            "task_id": str(self.task_id),
            "amendment_version": self.amendment_version,
            "control_epoch": self.control_epoch,
            "dispatch_epoch": self.dispatch_epoch,
            "matrix_event_id": self.matrix_event_id,
            "effective_limits": self.effective_limits,
        }


def effective_run_limits(
    session: Session,
    tenant_id: UUID,
    run_id: UUID,
    *,
    manifest_limits: dict[str, object] | None = None,
) -> dict[str, int]:
    row = session.execute(
        select(
            run_limit_amendment.c.model_calls,
            run_limit_amendment.c.input_tokens,
            run_limit_amendment.c.output_tokens,
        )
        .where(run_limit_amendment.c.tenant_id == tenant_id, run_limit_amendment.c.run_id == run_id)
        .order_by(run_limit_amendment.c.amendment_version.desc())
        .limit(1)
    ).mappings().one_or_none()
    if row is not None:
        return {name: int(row[name]) for name in DEMO_LIMIT_CEILINGS}
    if manifest_limits is None:
        manifest = session.execute(
            select(run_manifest.c.frozen_config).where(
                run_manifest.c.tenant_id == tenant_id,
                run_manifest.c.run_id == run_id,
            )
        ).scalar_one()
        manifest_limits = manifest.get("limits", {})
    return {name: int(manifest_limits.get(name, 0)) for name in DEMO_LIMIT_CEILINGS}


class RunLimitAmendmentApplication:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def amend(
        self,
        actor: Actor,
        run_id: UUID,
        *,
        task_id: UUID,
        matrix_event_id: str,
        expected_control_epoch: int,
        expected_dispatch_epoch: int,
        expected_amendment_version: int,
        model_calls: int,
        input_tokens: int,
        output_tokens: int,
        reason: str,
        idempotency_key: str,
        correlation_id: UUID,
    ) -> RunLimitAmendmentProjection:
        if os.getenv("LAUNCHSCOPE_DEMO_MODE", "").strip().lower() != "true":
            raise NotFoundError("Demo Run limit amendments are disabled")
        limits = {
            "model_calls": model_calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        if not matrix_event_id.strip() or not reason.strip() or not idempotency_key.strip():
            raise RunLimitAmendmentError("event, reason and idempotency key are required")
        for name, value in limits.items():
            if value <= 0 or value > DEMO_LIMIT_CEILINGS[name]:
                raise RunLimitAmendmentError(f"{name} is outside the bounded Demo ceiling")
        request_hash = _request_hash(
            run_id,
            task_id,
            matrix_event_id,
            expected_control_epoch,
            expected_dispatch_epoch,
            expected_amendment_version,
            limits,
            reason,
        )
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            existing = session.execute(
                select(run_limit_amendment).where(
                    run_limit_amendment.c.tenant_id == actor.tenant_id,
                    run_limit_amendment.c.idempotency_key == idempotency_key,
                )
            ).mappings().one_or_none()
            if existing is not None:
                if existing["request_sha256"] != request_hash:
                    raise RunLimitAmendmentConflict("IDEMPOTENCY_CONFLICT")
                return _projection(existing)

            run = session.execute(
                select(evaluation_run)
                .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                .with_for_update()
            ).mappings().one_or_none()
            assigned = session.execute(
                select(task)
                .where(task.c.tenant_id == actor.tenant_id, task.c.run_id == run_id, task.c.id == task_id)
                .with_for_update()
            ).mappings().one_or_none()
            control = session.execute(
                select(run_execution_control)
                .where(
                    run_execution_control.c.tenant_id == actor.tenant_id,
                    run_execution_control.c.run_id == run_id,
                )
                .with_for_update()
            ).mappings().one_or_none()
            if run is None or assigned is None or control is None:
                raise NotFoundError("Run/Task execution state was not found")
            if (
                run["status"] != "NEEDS_ATTENTION"
                or run["last_failure_class"] != "BUDGET"
                or assigned["status"] != "NEEDS_ATTENTION"
                or assigned["last_failure_class"] != "BUDGET"
                or not assigned["required"]
            ):
                raise RunLimitAmendmentError("Run and required Task must be Budget-blocked")
            if int(control["control_epoch"]) != expected_control_epoch:
                raise RunLimitAmendmentConflict("execution-control epoch is stale")
            if int(assigned["dispatch_epoch"]) != expected_dispatch_epoch:
                raise RunLimitAmendmentConflict("Task dispatch epoch is stale")
            active = session.execute(
                select(func.count()).select_from(model_invocation).where(
                    model_invocation.c.tenant_id == actor.tenant_id,
                    model_invocation.c.run_id == run_id,
                    or_(
                        model_invocation.c.status.in_(("STARTED", "SUBMITTED")),
                        (
                            (model_invocation.c.status == "SUBMISSION_UNKNOWN")
                            & (model_invocation.c.task_id == task_id)
                            & (model_invocation.c.dispatch_epoch == expected_dispatch_epoch)
                        ),
                    ),
                )
            ).scalar_one()
            if int(active) != 0:
                raise RunLimitAmendmentError("Run has an active or uncertain model invocation")
            receipt = session.execute(
                select(matrix_event_receipt).where(
                    matrix_event_receipt.c.tenant_id == actor.tenant_id,
                    matrix_event_receipt.c.run_id == run_id,
                    matrix_event_receipt.c.task_id == task_id,
                    matrix_event_receipt.c.matrix_event_id == matrix_event_id,
                    matrix_event_receipt.c.processing_status == "PROCESSED",
                )
            ).mappings().one_or_none()
            if receipt is None:
                raise RunLimitAmendmentError("the exact current Matrix result was not received")
            latest = session.execute(
                select(run_limit_amendment)
                .where(run_limit_amendment.c.tenant_id == actor.tenant_id, run_limit_amendment.c.run_id == run_id)
                .order_by(run_limit_amendment.c.amendment_version.desc())
                .limit(1)
                .with_for_update()
            ).mappings().one_or_none()
            current_version = int(latest["amendment_version"]) if latest is not None else 0
            if current_version != expected_amendment_version:
                raise RunLimitAmendmentConflict("amendment version is stale")
            current_limits = effective_run_limits(session, actor.tenant_id, run_id)
            if any(limits[name] < current_limits[name] for name in limits) or limits == current_limits:
                raise RunLimitAmendmentError("effective limits must increase monotonically")
            used_tokens = int(session.execute(
                select(func.coalesce(func.sum(usage_record.c.quantity), 0)).where(
                    usage_record.c.tenant_id == actor.tenant_id,
                    usage_record.c.run_id == run_id,
                    usage_record.c.category == "model",
                )
            ).scalar_one())
            used_calls = int(session.execute(
                select(func.coalesce(func.sum(usage_record.c.quantity), 0)).where(
                    usage_record.c.tenant_id == actor.tenant_id,
                    usage_record.c.run_id == run_id,
                    usage_record.c.category == "model_calls",
                )
            ).scalar_one())
            used_input, used_output = session.execute(
                select(
                    func.coalesce(func.sum(model_invocation.c.prompt_tokens), 0),
                    func.coalesce(func.sum(model_invocation.c.completion_tokens), 0),
                ).where(
                    model_invocation.c.tenant_id == actor.tenant_id,
                    model_invocation.c.run_id == run_id,
                    model_invocation.c.status == "SETTLED",
                )
            ).one()
            if (
                used_calls > model_calls
                or used_tokens > input_tokens + output_tokens
                or int(used_input) > input_tokens
                or int(used_output) > output_tokens
            ):
                raise RunLimitAmendmentError("new effective limits do not cover already settled model usage")

            amendment_id = uuid4()
            version = current_version + 1
            session.execute(
                insert(run_limit_amendment).values(
                    id=amendment_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    amendment_version=version,
                    dispatch_epoch=expected_dispatch_epoch,
                    control_epoch=expected_control_epoch,
                    matrix_event_id=matrix_event_id,
                    matrix_payload_sha256=receipt["payload_sha256"],
                    **limits,
                    reason=reason.strip(),
                    authorized_by=actor.actor_id,
                    idempotency_key=idempotency_key,
                    request_sha256=request_hash,
                    correlation_id=correlation_id,
                    created_at=now,
                )
            )
            session.execute(
                update(task)
                .where(task.c.tenant_id == actor.tenant_id, task.c.id == task_id)
                .values(status="RUNNING", last_failure_class=None, last_error=None, updated_at=now)
            )
            session.execute(
                update(run_execution_control)
                .where(run_execution_control.c.tenant_id == actor.tenant_id, run_execution_control.c.run_id == run_id)
                .values(
                    state="ACTIVE",
                    requested_by=actor.actor_id,
                    pause_reason=None,
                    usage_settlement_status="NONE",
                    in_flight_count=0,
                    resumed_at=now,
                    last_error=None,
                    updated_at=now,
                )
            )
            session.execute(
                update(evaluation_run)
                .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                .values(status="RUNNING", last_failure_class=None, attention_reason=None, updated_at=now)
            )
            session.execute(
                insert(run_status_history).values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    from_status="NEEDS_ATTENTION",
                    to_status="RUNNING",
                    reason="Authorized exact-result processing under higher Demo model limits",
                    failure_class="BUDGET",
                    occurred_at=now,
                )
            )
            session.execute(
                insert(audit_event).values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    actor_type="USER",
                    action="run.limit_amended",
                    outcome="SUCCEEDED",
                    payload_sha256=request_hash,
                    metadata={
                        "amendment_version": version,
                        "task_id": str(task_id),
                        "matrix_event_id": matrix_event_id,
                        "correlation_id": str(correlation_id),
                    },
                    occurred_at=now,
                )
            )
            return RunLimitAmendmentProjection(
                amendment_id=amendment_id,
                run_id=run_id,
                task_id=task_id,
                amendment_version=version,
                control_epoch=expected_control_epoch,
                dispatch_epoch=expected_dispatch_epoch,
                matrix_event_id=matrix_event_id,
                effective_limits=limits,
            )


def _projection(row: dict[str, object]) -> RunLimitAmendmentProjection:
    return RunLimitAmendmentProjection(
        amendment_id=UUID(str(row["id"])),
        run_id=UUID(str(row["run_id"])),
        task_id=UUID(str(row["task_id"])),
        amendment_version=int(str(row["amendment_version"])),
        control_epoch=int(str(row["control_epoch"])),
        dispatch_epoch=int(str(row["dispatch_epoch"])),
        matrix_event_id=str(row["matrix_event_id"]),
        effective_limits={name: int(str(row[name])) for name in DEMO_LIMIT_CEILINGS},
    )


def _request_hash(
    run_id: UUID,
    task_id: UUID,
    matrix_event_id: str,
    control_epoch: int,
    dispatch_epoch: int,
    amendment_version: int,
    limits: dict[str, int],
    reason: str,
) -> str:
    payload = {
        "run_id": str(run_id),
        "task_id": str(task_id),
        "matrix_event_id": matrix_event_id,
        "expected_control_epoch": control_epoch,
        "expected_dispatch_epoch": dispatch_epoch,
        "expected_amendment_version": amendment_version,
        "limits": limits,
        "reason": reason.strip(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
