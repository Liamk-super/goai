"""Generation-v4 Matrix transport adapter for deterministic control-plane applications."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    agentteams_task_delivery,
    budget_reservation,
    evaluation_run,
    matrix_event_receipt,
    matrix_handoff,
    run_canonical_event_recovery,
    run_canonical_event_replay,
    run_limit_amendment,
    run_limit_amendment_replay,
    run_manifest,
    task,
    usage_record,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.modules.evaluation.agentteams_usage import (
    AgentUsageReader,
    AgentUsageSnapshot,
    configured_usage_reader,
    usage_delta,
)
from launchscope_api.modules.evaluation.limit_amendment_application import effective_run_limits
from launchscope_api.modules.evaluation.model_reconciliation import reconcile_gateway_delivery_usage
from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_domain.value_objects import TenantScope
from launchscope_orchestrator.agentteams_bridge import MatrixSenderDirectory

from .audit_application import SupervisorAuditApplication
from .completion_application import SupervisorCompletionApplication
from .planning_application import ManagerPlanningApplication

_MESSAGE_AGENTS = {
    "ManagerPlanV1": "evaluation-manager",
    "ManagerPlanV2": "evaluation-manager",
    "AgentHandoffV3": None,
    "AgentHandoffV4": None,
    "AuditResultV3": "evidence-auditor",
    "AuditResultV4": "evidence-auditor",
    "ManagerSynthesisV1": "evaluation-manager",
    "ManagerSynthesisV2": "evaluation-manager",
}
_MAX_MESSAGE_BYTES = 512_000


def _control_plane_plan_document(message_type: str, document: dict[str, Any]) -> dict[str, Any]:
    if message_type != "ManagerPlanV2" or not isinstance(document.get("tasks"), list):
        return document
    changed = False
    tasks: list[object] = []
    for item in document["tasks"]:
        if not isinstance(item, dict) or not isinstance(item.get("tool_policy"), list):
            tasks.append(item)
            continue
        policy = [
            "launchscope-context.get.v2" if value == "launchscope-context.get.v1" else value
            for value in item["tool_policy"]
        ]
        changed = changed or policy != item["tool_policy"]
        tasks.append({**item, "tool_policy": policy})
    return {**document, "tasks": tasks} if changed else document


@dataclass(frozen=True, slots=True)
class SupervisorMatrixResult:
    matrix_event_id: str
    task_status: str
    run_status: str
    duplicate: bool = False
    report_id: UUID | None = None


class MatrixReceiptStore(Protocol):
    def seen(self, actor: Actor, matrix_event_id: str, payload_sha256: str, run_id: UUID) -> bool: ...

    def record(
        self,
        actor: Actor,
        *,
        run_id: UUID,
        task_id: UUID,
        room_id: str,
        matrix_event_id: str,
        sender_mxid: str,
        payload_sha256: str,
    ) -> None: ...


class V4DeliverySettlement(Protocol):
    def complete(self, actor: Actor, run_id: UUID, task_id: UUID) -> None: ...


class PostgresV4DeliverySettlement:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        usage_reader: AgentUsageReader | None = None,
    ) -> None:
        self._sessions = sessions
        self._usage_reader = configured_usage_reader() if usage_reader is None else usage_reader

    def prepare(self, actor: Actor, run_id: UUID, task_id: UUID) -> None:
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            delivery = session.execute(
                select(agentteams_task_delivery)
                .where(
                    agentteams_task_delivery.c.tenant_id == actor.tenant_id,
                    agentteams_task_delivery.c.run_id == run_id,
                    agentteams_task_delivery.c.task_id == task_id,
                )
                .order_by(agentteams_task_delivery.c.dispatch_epoch.desc())
                .limit(1)
                .with_for_update()
            ).mappings().one_or_none()
            if delivery is None:
                raise ValueError("generation-v4 result has no attributable delivery")
            gateway = reconcile_gateway_delivery_usage(
                session,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                task_id=task_id,
                dispatch_epoch=int(delivery["dispatch_epoch"]),
                agent_code=str(delivery["agent_code"]),
                now=now,
                usage_reader=self._usage_reader,
            )
            if gateway.handled:
                if gateway.failure_class is not None:
                    raise ValueError(gateway.reason or "generation-v4 gateway usage settlement failed")
                return
            if self._usage_reader is None or delivery["usage_baseline"] is None:
                raise ValueError("generation-v4 result has no complete model usage baseline")
            terminal = self._usage_reader.snapshot(str(delivery["agent_code"]))
            delta = usage_delta(
                AgentUsageSnapshot.from_dict(delivery["usage_baseline"]),
                terminal,
                task_key=f"{task_id}:{int(delivery['dispatch_epoch'])}",
            )
            key = f"provider:{delta.receipt_id}"
            existing = session.execute(
                select(usage_record.c.id).where(
                    usage_record.c.tenant_id == actor.tenant_id,
                    usage_record.c.idempotency_key == key,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return
            manifest = session.execute(
                select(run_manifest.c.frozen_config).where(
                    run_manifest.c.tenant_id == actor.tenant_id,
                    run_manifest.c.run_id == run_id,
                )
            ).scalar_one()
            limits = effective_run_limits(
                session,
                actor.tenant_id,
                run_id,
                manifest_limits=manifest.get("limits", {}),
            )
            prior_tokens = int(session.execute(
                select(func.coalesce(func.sum(usage_record.c.quantity), 0)).where(
                    usage_record.c.tenant_id == actor.tenant_id,
                    usage_record.c.run_id == run_id,
                    usage_record.c.category == "model",
                )
            ).scalar_one())
            prior_calls = int(session.execute(
                select(func.coalesce(func.sum(usage_record.c.quantity), 0)).where(
                    usage_record.c.tenant_id == actor.tenant_id,
                    usage_record.c.run_id == run_id,
                    usage_record.c.category == "model_calls",
                )
            ).scalar_one())
            token_total = delta.input_tokens + delta.output_tokens
            if prior_calls + delta.call_count > limits["model_calls"]:
                raise ValueError("model call limit reached")
            if prior_tokens + token_total > limits["input_tokens"] + limits["output_tokens"]:
                raise ValueError("model token limit reached")
            pricing = manifest.get("model_pricing", {})
            cost_mode = str(pricing.get("cost_mode") or "TOKEN_ONLY").upper()
            cost = None
            if cost_mode == "EXACT":
                try:
                    cost = (
                        Decimal(delta.input_tokens)
                        * Decimal(str(pricing["input_usd_per_million_tokens"]))
                        + Decimal(delta.output_tokens)
                        * Decimal(str(pricing["output_usd_per_million_tokens"]))
                    ) / Decimal(1_000_000)
                except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
                    raise ValueError("generation-v4 model pricing is incomplete") from exc
            elif cost_mode != "TOKEN_ONLY":
                raise ValueError("generation-v4 model cost mode is unsupported")
            reservation = None
            if cost is not None:
                reservation = session.execute(
                    select(budget_reservation)
                    .where(
                        budget_reservation.c.tenant_id == actor.tenant_id,
                        budget_reservation.c.run_id == run_id,
                        budget_reservation.c.category == "run_total",
                    )
                    .with_for_update()
                ).mappings().one()
                if reservation["consumed_amount"] + cost > reservation["limit_amount"]:
                    raise ValueError("Run budget limit reached")
            session.execute(
                insert(usage_record).values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    category="model",
                    quantity=token_total,
                    cost=cost or 0,
                    idempotency_key=key,
                    created_at=now,
                )
            )
            session.execute(
                insert(usage_record).values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    category="model_calls",
                    quantity=delta.call_count,
                    cost=0,
                    idempotency_key=f"{key}:calls",
                    created_at=now,
                )
            )
            if cost is None:
                session.execute(
                    insert(usage_record).values(
                        id=uuid4(),
                        tenant_id=actor.tenant_id,
                        run_id=run_id,
                        task_id=task_id,
                        category="model_cost_unavailable",
                        quantity=1,
                        cost=0,
                        idempotency_key=f"{key}:cost-unavailable",
                        created_at=now,
                    )
                )
            else:
                assert reservation is not None
                session.execute(
                    update(budget_reservation)
                    .where(
                        budget_reservation.c.id == reservation["id"],
                        budget_reservation.c.tenant_id == actor.tenant_id,
                    )
                    .values(
                        consumed_amount=reservation["consumed_amount"] + cost,
                        status="CONSUMED",
                        updated_at=now,
                    )
                )

    def complete(self, actor: Actor, run_id: UUID, task_id: UUID) -> None:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            session.execute(
                update(agentteams_task_delivery)
                .where(
                    agentteams_task_delivery.c.tenant_id == actor.tenant_id,
                    agentteams_task_delivery.c.run_id == run_id,
                    agentteams_task_delivery.c.task_id == task_id,
                    agentteams_task_delivery.c.status == "DELIVERED",
                )
                .values(status="COMPLETED", completed_at=datetime.now(UTC))
            )


class _NoopDeliverySettlement:
    def complete(self, actor: Actor, run_id: UUID, task_id: UUID) -> None:
        return None


class PostgresMatrixReceiptStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def seen(self, actor: Actor, matrix_event_id: str, payload_sha256: str, run_id: UUID) -> bool:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            existing = session.execute(
                select(matrix_event_receipt.c.payload_sha256).where(
                    matrix_event_receipt.c.tenant_id == actor.tenant_id,
                    matrix_event_receipt.c.matrix_event_id == matrix_event_id,
                )
            ).scalar_one_or_none()
            if existing is None:
                return False
            if existing != payload_sha256:
                raise ValueError("Matrix event ID was replayed with a different generation-v4 payload")
            visible = session.execute(
                select(evaluation_run.c.id).where(
                    evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id
                )
            ).scalar_one_or_none()
            if visible is None:
                raise ValueError("Matrix replay does not belong to the authenticated Run")
            return True

    def record(
        self,
        actor: Actor,
        *,
        run_id: UUID,
        task_id: UUID,
        room_id: str,
        matrix_event_id: str,
        sender_mxid: str,
        payload_sha256: str,
    ) -> None:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            session.execute(
                matrix_event_receipt.insert().values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    room_id=room_id,
                    matrix_event_id=matrix_event_id,
                    sender_mxid=sender_mxid,
                    payload_sha256=payload_sha256,
                    processing_status="PROCESSED_V4_CONTROL_PLANE",
                    created_at=datetime.now(UTC),
                )
            )

    def authorize_replay(
        self,
        actor: Actor,
        *,
        run_id: UUID,
        task_id: UUID,
        matrix_event_id: str,
        payload_sha256: str,
        sender_mxid: str,
        message_type: str,
    ) -> UUID | None:
        if message_type not in _MESSAGE_AGENTS:
            return None
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            assigned = session.execute(
                select(task)
                .where(
                    task.c.tenant_id == actor.tenant_id,
                    task.c.run_id == run_id,
                    task.c.id == task_id,
                )
                .with_for_update()
            ).mappings().one_or_none()
            receipt = session.execute(
                select(matrix_event_receipt).where(
                    matrix_event_receipt.c.tenant_id == actor.tenant_id,
                    matrix_event_receipt.c.run_id == run_id,
                    matrix_event_receipt.c.task_id == task_id,
                    matrix_event_receipt.c.matrix_event_id == matrix_event_id,
                )
            ).mappings().one_or_none()
            if assigned is None or receipt is None or assigned["status"] != "RUNNING":
                return None
            expected_agent = str(assigned["agent_identity_ref"]).split("@", 1)[0]
            if sender_mxid != receipt["sender_mxid"] or not expected_agent:
                return None
            synthetic = session.execute(
                select(matrix_handoff).where(
                    matrix_handoff.c.tenant_id == actor.tenant_id,
                    matrix_handoff.c.run_id == run_id,
                    matrix_handoff.c.task_id == task_id,
                    matrix_handoff.c.payload_sha256 == receipt["payload_sha256"],
                )
            ).mappings().one_or_none()
            if (
                receipt["payload_sha256"] == payload_sha256
                or receipt["processing_status"] != "PROCESSED"
                or synthetic is None
                or synthetic["sender_agent"] != expected_agent
                or synthetic["risk"] != "HIGH"
                or float(synthetic["confidence"]) != 0.0
                or synthetic["approval_required"] is not True
                or list(synthetic["evidence_ids"] or [])
            ):
                return None
            amendment = session.execute(
                select(run_limit_amendment)
                .outerjoin(
                    run_limit_amendment_replay,
                    (run_limit_amendment_replay.c.tenant_id == run_limit_amendment.c.tenant_id)
                    & (run_limit_amendment_replay.c.amendment_id == run_limit_amendment.c.id),
                )
                .where(
                    run_limit_amendment.c.tenant_id == actor.tenant_id,
                    run_limit_amendment.c.run_id == run_id,
                    run_limit_amendment.c.task_id == task_id,
                    run_limit_amendment.c.dispatch_epoch == int(assigned["dispatch_epoch"]),
                    run_limit_amendment.c.matrix_event_id == matrix_event_id,
                    run_limit_amendment.c.matrix_payload_sha256 == receipt["payload_sha256"],
                    run_limit_amendment_replay.c.id.is_(None),
                )
                .order_by(run_limit_amendment.c.amendment_version.desc())
                .limit(1)
            ).mappings().one_or_none()
            if amendment is not None:
                return UUID(str(amendment["id"]))
            recovery = session.execute(
                select(run_canonical_event_recovery)
                .outerjoin(
                    run_canonical_event_replay,
                    (run_canonical_event_replay.c.tenant_id == run_canonical_event_recovery.c.tenant_id)
                    & (run_canonical_event_replay.c.recovery_id == run_canonical_event_recovery.c.id),
                )
                .where(
                    run_canonical_event_recovery.c.tenant_id == actor.tenant_id,
                    run_canonical_event_recovery.c.run_id == run_id,
                    run_canonical_event_recovery.c.task_id == task_id,
                    run_canonical_event_recovery.c.dispatch_epoch == int(assigned["dispatch_epoch"]),
                    run_canonical_event_recovery.c.matrix_event_id == matrix_event_id,
                    run_canonical_event_recovery.c.source_payload_sha256 == receipt["payload_sha256"],
                    run_canonical_event_replay.c.id.is_(None),
                )
                .limit(1)
            ).mappings().one_or_none()
            return UUID(str(recovery["id"])) if recovery is not None else None

    def record_replay(
        self,
        actor: Actor,
        *,
        amendment_id: UUID,
        run_id: UUID,
        task_id: UUID,
        matrix_event_id: str,
        payload_sha256: str,
    ) -> None:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            existing = session.execute(
                select(run_limit_amendment_replay).where(
                    run_limit_amendment_replay.c.tenant_id == actor.tenant_id,
                    run_limit_amendment_replay.c.amendment_id == amendment_id,
                )
            ).mappings().one_or_none()
            if existing is not None:
                if existing["matrix_payload_sha256"] != payload_sha256:
                    raise ValueError("Run limit amendment was replayed with a different canonical payload")
                return
            is_amendment = session.execute(
                select(run_limit_amendment.c.id).where(
                    run_limit_amendment.c.tenant_id == actor.tenant_id,
                    run_limit_amendment.c.id == amendment_id,
                )
            ).scalar_one_or_none()
            if is_amendment is None:
                canonical = session.execute(
                    select(run_canonical_event_replay).where(
                        run_canonical_event_replay.c.tenant_id == actor.tenant_id,
                        run_canonical_event_replay.c.recovery_id == amendment_id,
                    )
                ).mappings().one_or_none()
                if canonical is not None:
                    if canonical["canonical_payload_sha256"] != payload_sha256:
                        raise ValueError("canonical event recovery was replayed with a different payload")
                    return
                session.execute(
                    insert(run_canonical_event_replay).values(
                        id=uuid4(), tenant_id=actor.tenant_id, recovery_id=amendment_id,
                        run_id=run_id, task_id=task_id, matrix_event_id=matrix_event_id,
                        canonical_payload_sha256=payload_sha256, created_at=datetime.now(UTC),
                    )
                )
                return
            session.execute(
                insert(run_limit_amendment_replay).values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    amendment_id=amendment_id,
                    run_id=run_id,
                    task_id=task_id,
                    matrix_event_id=matrix_event_id,
                    matrix_payload_sha256=payload_sha256,
                    created_at=datetime.now(UTC),
                )
            )


class SupervisorMatrixAdapter:
    def __init__(
        self,
        planning: ManagerPlanningApplication,
        audit: SupervisorAuditApplication,
        completion: SupervisorCompletionApplication,
        directory: MatrixSenderDirectory,
        receipts: MatrixReceiptStore,
        settlement: V4DeliverySettlement | None = None,
    ) -> None:
        self._planning = planning
        self._audit = audit
        self._completion = completion
        self._directory = directory
        self._receipts = receipts
        self._settlement = settlement or _NoopDeliverySettlement()

    @staticmethod
    def can_consume(raw_event: Mapping[str, object]) -> bool:
        content = raw_event.get("content")
        return isinstance(content, Mapping) and content.get("message_type") in _MESSAGE_AGENTS

    def consume(
        self, actor: Actor, raw_event: Mapping[str, object], *, run_id: UUID, task_id: UUID
    ) -> SupervisorMatrixResult:
        event_id = str(raw_event.get("event_id") or "")
        room_id = str(raw_event.get("room_id") or "")
        sender = str(raw_event.get("sender") or "")
        content = raw_event.get("content")
        if not event_id or not room_id or not sender or not isinstance(content, Mapping):
            raise ValueError("generation-v4 Matrix event lacks immutable identity fields")
        message_type = str(content.get("message_type") or "")
        if message_type not in _MESSAGE_AGENTS:
            raise ValueError("unsupported generation-v4 Matrix message type")
        serialized = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()
        if len(serialized) > _MAX_MESSAGE_BYTES:
            raise ValueError("generation-v4 Matrix message exceeds the bounded transport size")
        digest = hashlib.sha256(serialized).hexdigest()
        sender_agent = self._directory.agent_for_mxid(sender)
        expected_agent = _MESSAGE_AGENTS[message_type]
        if sender_agent is None or (expected_agent is not None and sender_agent != expected_agent):
            raise ValueError("Matrix sender does not own the generation-v4 message role")
        replay_amendment_id = None
        try:
            if self._receipts.seen(actor, event_id, digest, run_id):
                return SupervisorMatrixResult(event_id, "DUPLICATE", "UNCHANGED", duplicate=True)
        except ValueError:
            authorize_replay = getattr(self._receipts, "authorize_replay", None)
            replay_amendment_id = (
                authorize_replay(
                    actor,
                    run_id=run_id,
                    task_id=task_id,
                    matrix_event_id=event_id,
                    payload_sha256=digest,
                    sender_mxid=sender,
                    message_type=message_type,
                )
                if authorize_replay is not None
                else None
            )
            if replay_amendment_id is None:
                raise

        prepare_settlement = getattr(self._settlement, "prepare", None)
        if prepare_settlement is not None:
            prepare_settlement(actor, run_id, task_id)

        if message_type in {"AuditResultV3", "AuditResultV4"}:
            documents = content.get("documents")
            if not isinstance(documents, list) or not all(isinstance(item, dict) for item in documents):
                raise ValueError(f"{message_type} transport must contain a documents array")
            specialist_report_ref = content.get("specialist_report_ref")
            if specialist_report_ref is not None and not isinstance(specialist_report_ref, dict):
                raise ValueError(f"{message_type} specialist_report_ref must be an object")
            specialist_report = content.get("specialist_report")
            if specialist_report is not None and not isinstance(specialist_report, dict):
                raise ValueError(f"{message_type} specialist_report must be an object")
            if specialist_report_ref is not None and specialist_report is not None:
                audit_result = self._audit.submit_audit_results(
                    actor,
                    run_id,
                    documents,
                    task_id=task_id,
                    specialist_report_ref=specialist_report_ref,
                    specialist_report=specialist_report,
                )
            elif specialist_report_ref is not None:
                audit_result = self._audit.submit_audit_results(
                    actor,
                    run_id,
                    documents,
                    task_id=task_id,
                    specialist_report_ref=specialist_report_ref,
                )
            elif specialist_report is not None:
                audit_result = self._audit.submit_audit_results(
                    actor,
                    run_id,
                    documents,
                    task_id=task_id,
                    specialist_report=specialist_report,
                )
            else:
                audit_result = self._audit.submit_audit_results(actor, run_id, documents, task_id=task_id)
            task_status = "NEEDS_ATTENTION" if audit_result.state == "NEEDS_ATTENTION" else "SUCCEEDED"
            run_status = audit_result.state
            if audit_result.state == "DETERMINISTIC_SCORING":
                self._completion.prepare_scoring(actor, run_id)
                run_status = "SUPERVISOR_SYNTHESIS"
            report_id = None
        else:
            document = content.get("document")
            if not isinstance(document, dict):
                raise ValueError(f"{message_type} transport must contain one document object")
            if message_type in {"ManagerPlanV1", "ManagerPlanV2"}:
                self._planning.accept_and_materialize(
                    actor,
                    run_id,
                    task_id,
                    _control_plane_plan_document(message_type, document),
                )
                task_status, run_status, report_id = "SUCCEEDED", "DOMAIN_REVIEW", None
            elif message_type in {"AgentHandoffV3", "AgentHandoffV4"}:
                if sender_agent != document.get("agent_code"):
                    raise ValueError(f"Matrix sender does not match {message_type}.agent_code")
                specialist_report = content.get("specialist_report")
                if specialist_report is not None and not isinstance(specialist_report, dict):
                    raise ValueError(f"{message_type} specialist_report must be an object")
                domain_result = (
                    self._audit.ingest_domain_handoff(
                        actor,
                        run_id,
                        task_id,
                        document,
                        specialist_report=specialist_report,
                    )
                    if specialist_report is not None
                    else self._audit.ingest_domain_handoff(actor, run_id, task_id, document)
                )
                task_status, run_status, report_id = domain_result.state, domain_result.state, None
            else:
                completion_result = self._completion.commit_synthesis_report(actor, run_id, task_id, document)
                task_status, run_status, report_id = "SUCCEEDED", "COMPLETED", completion_result.report_id

        self._settlement.complete(actor, run_id, task_id)
        if replay_amendment_id is not None:
            self._receipts.record_replay(  # type: ignore[attr-defined]
                actor,
                amendment_id=replay_amendment_id,
                run_id=run_id,
                task_id=task_id,
                matrix_event_id=event_id,
                payload_sha256=digest,
            )
        else:
            self._receipts.record(
                actor,
                run_id=run_id,
                task_id=task_id,
                room_id=room_id,
                matrix_event_id=event_id,
                sender_mxid=sender,
                payload_sha256=digest,
            )
        return SupervisorMatrixResult(event_id, task_status, run_status, report_id=report_id)


class GenerationAwareMatrixIngress:
    def __init__(self, supervisor: SupervisorMatrixAdapter, legacy: Any) -> None:
        self._supervisor = supervisor
        self._legacy = legacy

    def consume(self, actor: Actor, raw_event: dict[str, object], *, run_id: UUID, task_id: UUID) -> Any:
        if self._supervisor.can_consume(raw_event):
            return self._supervisor.consume(actor, raw_event, run_id=run_id, task_id=task_id)
        return self._legacy.consume(actor, raw_event, run_id=run_id, task_id=task_id)


__all__ = [
    "GenerationAwareMatrixIngress",
    "PostgresV4DeliverySettlement",
    "PostgresMatrixReceiptStore",
    "SupervisorMatrixAdapter",
    "SupervisorMatrixResult",
]
