"""Demo-only recovery of a canonical Matrix event masked by a synthetic failure."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    audit_event,
    evaluation_run,
    matrix_event_receipt,
    matrix_handoff,
    model_invocation,
    run_canonical_event_recovery,
    run_execution_control,
    run_status_history,
    task,
    usage_record,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_domain.value_objects import TenantScope


class CanonicalEventRecoveryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CanonicalEventRecoveryProjection:
    recovery_id: UUID
    run_id: UUID
    task_id: UUID
    dispatch_epoch: int
    control_epoch: int
    matrix_event_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "recovery_id": str(self.recovery_id),
            "run_id": str(self.run_id),
            "task_id": str(self.task_id),
            "dispatch_epoch": self.dispatch_epoch,
            "control_epoch": self.control_epoch,
            "matrix_event_id": self.matrix_event_id,
        }


class CanonicalEventRecoveryApplication:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def recover(
        self,
        actor: Actor,
        run_id: UUID,
        *,
        task_id: UUID,
        matrix_event_id: str,
        expected_control_epoch: int,
        expected_dispatch_epoch: int,
        reason: str,
        idempotency_key: str,
        correlation_id: UUID,
    ) -> CanonicalEventRecoveryProjection:
        if os.getenv("LAUNCHSCOPE_DEMO_MODE", "").strip().lower() != "true":
            raise NotFoundError("canonical Matrix event recovery is disabled")
        if not matrix_event_id.strip() or not reason.strip() or not idempotency_key.strip():
            raise CanonicalEventRecoveryError("event, reason and idempotency key are required")
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "run_id": str(run_id),
                    "task_id": str(task_id),
                    "matrix_event_id": matrix_event_id,
                    "expected_control_epoch": expected_control_epoch,
                    "expected_dispatch_epoch": expected_dispatch_epoch,
                    "reason": reason.strip(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            existing = session.execute(
                select(run_canonical_event_recovery).where(
                    run_canonical_event_recovery.c.tenant_id == actor.tenant_id,
                    run_canonical_event_recovery.c.idempotency_key == idempotency_key,
                )
            ).mappings().one_or_none()
            if existing is not None:
                if existing["request_sha256"] != request_hash:
                    raise CanonicalEventRecoveryError("IDEMPOTENCY_CONFLICT")
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
                .where(run_execution_control.c.tenant_id == actor.tenant_id, run_execution_control.c.run_id == run_id)
                .with_for_update()
            ).mappings().one_or_none()
            if run is None or assigned is None or control is None:
                raise NotFoundError("Run/Task execution state was not found")
            if (
                run["status"] != "NEEDS_ATTENTION"
                or run["last_failure_class"] != "SUBMISSION_UNKNOWN"
                or assigned["status"] != "NEEDS_ATTENTION"
                or assigned["last_failure_class"] != "SUBMISSION_UNKNOWN"
                or "provider receipt was reused" not in str(assigned["last_error"] or "")
            ):
                raise CanonicalEventRecoveryError("Run and Task are not eligible for canonical event recovery")
            if int(control["control_epoch"]) != expected_control_epoch:
                raise CanonicalEventRecoveryError("execution-control epoch is stale")
            if int(assigned["dispatch_epoch"]) != expected_dispatch_epoch:
                raise CanonicalEventRecoveryError("Task dispatch epoch is stale")
            active = int(session.execute(
                select(func.count()).select_from(model_invocation).where(
                    model_invocation.c.tenant_id == actor.tenant_id,
                    model_invocation.c.run_id == run_id,
                    model_invocation.c.task_id == task_id,
                    model_invocation.c.dispatch_epoch == expected_dispatch_epoch,
                    model_invocation.c.status.in_(("STARTED", "SUBMITTED", "SUBMISSION_UNKNOWN")),
                )
            ).scalar_one())
            if active:
                raise CanonicalEventRecoveryError("the current delivery still has an active or uncertain model call")
            receipt = session.execute(
                select(matrix_event_receipt).where(
                    matrix_event_receipt.c.tenant_id == actor.tenant_id,
                    matrix_event_receipt.c.run_id == run_id,
                    matrix_event_receipt.c.task_id == task_id,
                    matrix_event_receipt.c.matrix_event_id == matrix_event_id,
                    matrix_event_receipt.c.processing_status == "PROCESSED",
                )
            ).mappings().one_or_none()
            synthetic = (
                session.execute(
                    select(matrix_handoff).where(
                        matrix_handoff.c.tenant_id == actor.tenant_id,
                        matrix_handoff.c.run_id == run_id,
                        matrix_handoff.c.task_id == task_id,
                        matrix_handoff.c.payload_sha256 == receipt["payload_sha256"],
                    )
                ).mappings().one_or_none()
                if receipt is not None
                else None
            )
            if (
                receipt is None
                or synthetic is None
                or synthetic["risk"] != "HIGH"
                or float(synthetic["confidence"]) != 0.0
                or synthetic["approval_required"] is not True
                or list(synthetic["evidence_ids"] or [])
            ):
                raise CanonicalEventRecoveryError("the exact synthetic failure projection was not found")
            usage_categories = set(
                session.execute(
                    select(usage_record.c.category).where(
                        usage_record.c.tenant_id == actor.tenant_id,
                        usage_record.c.run_id == run_id,
                        usage_record.c.task_id == task_id,
                        usage_record.c.category.in_(("model", "model_calls")),
                    )
                ).scalars()
            )
            if usage_categories != {"model", "model_calls"}:
                raise CanonicalEventRecoveryError("the current result has no settled model usage")
            recovery_id = uuid4()
            session.execute(
                insert(run_canonical_event_recovery).values(
                    id=recovery_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    dispatch_epoch=expected_dispatch_epoch,
                    control_epoch=expected_control_epoch,
                    matrix_event_id=matrix_event_id,
                    source_payload_sha256=receipt["payload_sha256"],
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
                update(evaluation_run)
                .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                .values(status="RUNNING", last_failure_class=None, attention_reason=None, updated_at=now)
            )
            session.execute(
                insert(run_status_history).values(
                    id=uuid4(), tenant_id=actor.tenant_id, run_id=run_id,
                    from_status="NEEDS_ATTENTION", to_status="RUNNING",
                    reason="Authorized processing of an already settled canonical Matrix result",
                    failure_class="SUBMISSION_UNKNOWN", occurred_at=now,
                )
            )
            session.execute(
                insert(audit_event).values(
                    id=uuid4(), tenant_id=actor.tenant_id, run_id=run_id,
                    actor_type="USER", action="run.canonical_event_recovered", outcome="SUCCEEDED",
                    payload_sha256=request_hash,
                    metadata={"task_id": str(task_id), "matrix_event_id": matrix_event_id},
                    occurred_at=now,
                )
            )
            return CanonicalEventRecoveryProjection(
                recovery_id, run_id, task_id, expected_dispatch_epoch, expected_control_epoch, matrix_event_id
            )


def _projection(row: RowMapping) -> CanonicalEventRecoveryProjection:
    return CanonicalEventRecoveryProjection(
        UUID(str(row["id"])), UUID(str(row["run_id"])), UUID(str(row["task_id"])),
        int(str(row["dispatch_epoch"])), int(str(row["control_epoch"])), str(row["matrix_event_id"]),
    )
