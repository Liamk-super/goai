"""Idempotent Matrix result consumer and deterministic v0.2 stage gates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    budget_reservation,
    decision,
    decision_finding,
    evaluation_run,
    evidence,
    evidence_audit,
    finding,
    finding_evidence,
    matrix_event_receipt,
    matrix_handoff,
    report,
    run_limit_amendment,
    run_limit_amendment_replay,
    run_manifest,
    run_status_history,
    stage,
    task,
    usage_record,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.infrastructure.object_store import S3QuarantineObjectStore
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_domain.value_objects import TenantScope
from launchscope_orchestrator.agentteams_bridge import (
    AcceptedMatrixEvent,
    AgentTeamsBridge,
    AuditResultV1,
    AuditResultV2,
    MatrixSenderDirectory,
)

from .agentteams_delivery import complete_task_delivery, task_usage_baseline
from .agentteams_usage import AgentUsageReader, configured_usage_reader, usage_delta
from .clarification_application import pause_run_for_clarification, record_information_requests
from .limit_amendment_application import effective_run_limits
from .model_reconciliation import reconcile_gateway_delivery_usage
from .task_dispatch import enqueue_ready_tasks, provider_cost_mode, provider_usage_required

_DIMENSIONS = (
    "PRODUCT_IMPLEMENTATION", "USER_USAGE", "BUSINESS_INVESTMENT", "GEO_POLICY_TREND",
)
_RANK = {"INSUFFICIENT_EVIDENCE": 0, "WEAK": 1, "MODERATE": 2, "STRONG": 3}
_GRADE = {value: key for key, value in _RANK.items()}
_DOMAIN_BY_AGENT = {
    "product-engineering": "PRODUCT_IMPLEMENTATION",
    "user-evidence": "USER_USAGE",
    "business-investment": "BUSINESS_INVESTMENT",
    "geo-policy-trend": "GEO_POLICY_TREND",
}
# A Task may only be mutated by a handoff while it is still awaiting its result.
# Epoch validation proves a message claims the current dispatch; this proves the
# durable Task has not already been settled, parked or cancelled, so a second
# distinct Matrix event for the same epoch cannot overwrite a committed result
# or re-advance a stage gate.
_AWAITING_RESULT_STATUSES = frozenset({"READY", "RUNNING", "LEASED"})
_MAX_PERSISTED_DIAGNOSTIC_CHARS = 1000


def _persisted_diagnostic(value: str) -> str:
    """Fit a Matrix diagnostic into legacy summary columns without changing the source event."""

    if len(value) <= _MAX_PERSISTED_DIAGNOSTIC_CHARS:
        return value
    return value[: _MAX_PERSISTED_DIAGNOSTIC_CHARS - 3] + "..."


@dataclass(frozen=True, slots=True)
class HandoffResult:
    matrix_event_id: str
    task_status: str
    run_status: str
    duplicate: bool = False
    report_id: UUID | None = None


class HandoffApplication:
    """Persist one immutable handoff and advance only satisfied durable dependencies."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        objects: S3QuarantineObjectStore,
        directory: MatrixSenderDirectory,
        *,
        require_provider_usage: bool | None = None,
        usage_reader: AgentUsageReader | None = None,
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._directory = directory
        self._bridge = AgentTeamsBridge()
        self._require_provider_usage = (
            provider_usage_required() if require_provider_usage is None else require_provider_usage
        )
        self._usage_reader = configured_usage_reader() if usage_reader is None else usage_reader

    def consume(self, actor: Actor, raw_event: dict[str, object], *, run_id: UUID, task_id: UUID) -> HandoffResult:
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            assigned = session.execute(
                select(task).where(
                    task.c.tenant_id == actor.tenant_id, task.c.run_id == run_id, task.c.id == task_id
                ).with_for_update()
            ).mappings().first()
            if assigned is None:
                raise NotFoundError("Run/Task assignment was not found")
            # ADR 0004: a clarification resume re-dispatches the same Task with a
            # higher epoch.  Rejecting a stale echo here keeps a result computed
            # before the user answered from overwriting the current attempt.
            accepted = self._bridge.accept_matrix_event(
                raw_event,
                self._directory,
                expected_run_id=run_id,
                expected_task_id=task_id,
                expected_dispatch_epoch=int(str(assigned.get("dispatch_epoch") or 0)),
            )
            if accepted.handoff.tenant_id != actor.tenant_id:
                raise ValueError("handoff Tenant does not match the authenticated routing scope")
            expected_agent = str(assigned["agent_identity_ref"]).split("@", 1)[0]
            if accepted.handoff.agent_code != expected_agent:
                raise ValueError("handoff Agent does not own the durable Task")
            prior = session.execute(
                select(matrix_event_receipt.c.payload_sha256).where(
                    matrix_event_receipt.c.tenant_id == actor.tenant_id,
                    matrix_event_receipt.c.matrix_event_id == accepted.matrix_event_id,
                )
            ).scalar_one_or_none()
            replay_amendment = None
            if prior is not None:
                if prior != accepted.payload_sha256:
                    raise ValueError("Matrix event ID was replayed with a different payload")
                if str(assigned["status"]) == "RUNNING":
                    replay_amendment = session.execute(
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
                            run_limit_amendment.c.dispatch_epoch == int(str(assigned.get("dispatch_epoch") or 0)),
                            run_limit_amendment.c.matrix_event_id == accepted.matrix_event_id,
                            run_limit_amendment.c.matrix_payload_sha256 == accepted.payload_sha256,
                            run_limit_amendment_replay.c.id.is_(None),
                        )
                        .order_by(run_limit_amendment.c.amendment_version.desc())
                        .limit(1)
                    ).mappings().one_or_none()
                if replay_amendment is None:
                    status = session.execute(select(evaluation_run.c.status).where(
                        evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id
                    )).scalar_one()
                    return HandoffResult(accepted.matrix_event_id, assigned["status"], status, duplicate=True)

            stored_status = str(assigned["status"])
            if stored_status not in _AWAITING_RESULT_STATUSES:
                # Record the receipt so the redelivery is auditable, then report it
                # as a duplicate instead of re-running result processing.
                session.execute(matrix_event_receipt.insert().values(
                    id=uuid4(), tenant_id=actor.tenant_id, run_id=run_id, task_id=task_id,
                    room_id=accepted.room_id, matrix_event_id=accepted.matrix_event_id,
                    sender_mxid=accepted.sender_mxid, payload_sha256=accepted.payload_sha256,
                    processing_status="IGNORED_TASK_NOT_AWAITING_RESULT", created_at=now,
                ))
                run_status = session.execute(select(evaluation_run.c.status).where(
                    evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id
                )).scalar_one()
                return HandoffResult(accepted.matrix_event_id, stored_status, run_status, duplicate=True)
            if replay_amendment is None:
                session.execute(matrix_event_receipt.insert().values(
                    id=uuid4(), tenant_id=actor.tenant_id, run_id=run_id, task_id=task_id,
                    room_id=accepted.room_id, matrix_event_id=accepted.matrix_event_id,
                    sender_mxid=accepted.sender_mxid, payload_sha256=accepted.payload_sha256,
                    processing_status="PROCESSED", created_at=now,
                ))
            handoff = accepted.handoff
            if replay_amendment is None:
                session.execute(matrix_handoff.insert().values(
                    id=uuid4(), tenant_id=actor.tenant_id, run_id=run_id, task_id=task_id,
                    dispatch_epoch=int(str(assigned.get("dispatch_epoch") or 0)),
                    room_id=accepted.room_id, sender_agent=handoff.agent_code,
                    receiver_agent="evaluation-manager", kind="RESULT", finding_id=None,
                    evidence_ids=[str(value) for value in handoff.evidence_refs], risk=handoff.risk,
                    confidence=Decimal(str(handoff.confidence)), approval_required=handoff.needs_human_approval,
                    payload_sha256=accepted.payload_sha256, created_at=now,
                ))
            usage_failure = self._record_provider_usage(
                session, actor.tenant_id, run_id, task_id, raw_event, now,
                agent_code=handoff.agent_code,
                dispatch_epoch=int(str(assigned.get("dispatch_epoch") or 0)),
                required=self._require_provider_usage,
            )
            complete_task_delivery(
                session,
                actor.tenant_id,
                task_id,
                int(str(assigned.get("dispatch_epoch") or 0)),
                now,
            )
            if usage_failure is not None:
                failure, reason = usage_failure
                session.execute(update(task).where(task.c.id == task_id).values(
                    status="NEEDS_ATTENTION", last_failure_class=failure, last_error=reason, updated_at=now,
                ))
                self._set_run(session, actor.tenant_id, run_id, "NEEDS_ATTENTION", now, reason, failure)
                return HandoffResult(accepted.matrix_event_id, "NEEDS_ATTENTION", "NEEDS_ATTENTION")
            if handoff.status == "NEEDS_INPUT":
                # ADR 0004: a recoverable product question, not a fail-closed failure.
                record_information_requests(
                    session, actor.tenant_id, run_id, task_id,
                    str(assigned["agent_identity_ref"]), handoff.information_requests, now,
                )
                pause_run_for_clarification(
                    session, actor.tenant_id, run_id, now,
                    _persisted_diagnostic(
                        f"{handoff.agent_code} needs user input: {handoff.next_action}"
                    ),
                )
                return HandoffResult(accepted.matrix_event_id, "NEEDS_INPUT", "WAITING_FOR_USER")
            if handoff.status != "SUCCEEDED":
                failure = handoff.failure_class or "SUBMISSION_UNKNOWN"
                session.execute(update(task).where(task.c.id == task_id).values(
                    status="NEEDS_ATTENTION", last_failure_class=failure,
                    last_error=_persisted_diagnostic(handoff.next_action), updated_at=now,
                ))
                self._set_run(session, actor.tenant_id, run_id, "NEEDS_ATTENTION", now,
                              _persisted_diagnostic(
                                  f"Task result requires attention: {handoff.next_action}"
                              ), failure)
                return HandoffResult(accepted.matrix_event_id, "NEEDS_ATTENTION", "NEEDS_ATTENTION")

            if handoff.agent_code == "evidence-auditor":
                audit_results: list[AuditResultV1 | AuditResultV2] = [item for item in handoff.audit_results]
                audit_failure = self._audit_findings(
                    session, actor.tenant_id, run_id, audit_results, now
                )
                if audit_failure is not None:
                    session.execute(update(task).where(task.c.id == task_id).values(
                        status="NEEDS_ATTENTION", last_failure_class="VALIDATION",
                        last_error=audit_failure, updated_at=now,
                    ))
                    self._set_run(
                        session, actor.tenant_id, run_id, "NEEDS_ATTENTION", now,
                        audit_failure, "VALIDATION",
                    )
                    return HandoffResult(accepted.matrix_event_id, "NEEDS_ATTENTION", "NEEDS_ATTENTION")
            if handoff.agent_code in _DOMAIN_BY_AGENT:
                self._persist_findings(session, actor.tenant_id, accepted, now)
            if replay_amendment is not None:
                session.execute(run_limit_amendment_replay.insert().values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    amendment_id=replay_amendment["id"],
                    run_id=run_id,
                    task_id=task_id,
                    matrix_event_id=accepted.matrix_event_id,
                    matrix_payload_sha256=accepted.payload_sha256,
                    created_at=now,
                ))
            session.execute(update(task).where(task.c.id == task_id).values(status="SUCCEEDED", updated_at=now))
            report_id = self._advance(session, actor.tenant_id, run_id, assigned["stage_code"], now)
            run_status = session.execute(select(evaluation_run.c.status).where(
                evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id
            )).scalar_one()
            return HandoffResult(accepted.matrix_event_id, "SUCCEEDED", run_status, report_id=report_id)

    def _record_provider_usage(
        self,
        session: Session, tenant_id: UUID, run_id: UUID, task_id: UUID,
        raw_event: dict[str, object], now: datetime, *, agent_code: str,
        dispatch_epoch: int, required: bool,
    ) -> tuple[str, str] | None:
        content = raw_event.get("content")
        usage = content.get("provider_usage") if isinstance(content, dict) else None
        manifest = session.execute(select(run_manifest.c.frozen_config).where(
            run_manifest.c.tenant_id == tenant_id, run_manifest.c.run_id == run_id,
        )).scalar_one()
        gateway_outcome = reconcile_gateway_delivery_usage(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=task_id,
            dispatch_epoch=dispatch_epoch,
            agent_code=agent_code,
            now=now,
            usage_reader=self._usage_reader,
        )
        if gateway_outcome.handled:
            if gateway_outcome.failure_class is not None:
                return (
                    gateway_outcome.failure_class,
                    gateway_outcome.reason or "gateway delivery usage reconciliation failed",
                )
            return None
        pricing = manifest.get("model_pricing", {})
        cost_mode = str(pricing.get("cost_mode") or provider_cost_mode()).upper()
        if cost_mode not in {"EXACT", "TOKEN_ONLY"}:
            return "POLICY", "Run Manifest contains an unsupported provider cost mode"
        if usage is None and self._usage_reader is not None:
            try:
                baseline = task_usage_baseline(session, tenant_id, task_id, dispatch_epoch)
                if baseline is None:
                    raise ValueError("delivery has no provider usage baseline")
                terminal = self._usage_reader.snapshot(agent_code)
                prices = {}
                if cost_mode == "EXACT":
                    prices = {
                        "input_usd_per_million": Decimal(str(pricing["input_usd_per_million_tokens"])),
                        "output_usd_per_million": Decimal(str(pricing["output_usd_per_million_tokens"])),
                    }
                receipt = usage_delta(
                    baseline,
                    terminal,
                    task_key=f"{task_id}:{dispatch_epoch}",
                    **prices,
                )
                usage = {
                    "receipt_id": receipt.receipt_id,
                    "input_tokens": receipt.input_tokens,
                    "output_tokens": receipt.output_tokens,
                    "call_count": receipt.call_count,
                    "submission_known": True,
                    "usage_known": True,
                    **({"cost_usd": str(receipt.cost_usd)} if receipt.cost_usd is not None else {}),
                }
            except Exception as exc:  # noqa: BLE001 - provider telemetry failures share one fail-closed result.
                if required:
                    return "SUBMISSION_UNKNOWN", f"provider usage receipt is unavailable: {exc}"
                return None
        if usage is None and not required:
            return None
        if (
            not isinstance(usage, dict)
            or usage.get("submission_known") is not True
            or usage.get("usage_known") is not True
        ):
            return "SUBMISSION_UNKNOWN", "model submission or usage state is unknown; automatic retry prohibited"
        try:
            input_tokens = int(usage["input_tokens"])
            output_tokens = int(usage["output_tokens"])
            call_count = int(usage.get("call_count", 1))
            receipt_id = str(usage["receipt_id"]).strip()
        except (KeyError, TypeError, ValueError):
            return "SUBMISSION_UNKNOWN", "provider usage receipt is incomplete; automatic retry prohibited"
        cost: Decimal | None = None
        if usage.get("cost_usd") is not None:
            try:
                cost = Decimal(str(usage["cost_usd"]))
            except (TypeError, ValueError):
                return "SUBMISSION_UNKNOWN", "provider cost receipt is invalid; automatic retry prohibited"
        elif cost_mode == "EXACT":
            return "BILLING_UNKNOWN", "provider cost receipt is required in EXACT mode"
        invalid_usage = input_tokens < 0 or output_tokens < 0 or call_count <= 0 or not receipt_id
        if invalid_usage or (cost is not None and cost < 0):
            return "SUBMISSION_UNKNOWN", "provider usage receipt is invalid; automatic retry prohibited"
        receipt_owner = session.execute(select(usage_record.c.run_id, usage_record.c.task_id).where(
            usage_record.c.tenant_id == tenant_id,
            usage_record.c.idempotency_key == f"provider:{receipt_id}",
        )).one_or_none()
        if receipt_owner is not None:
            if receipt_owner.run_id == run_id and receipt_owner.task_id == task_id:
                return None
            return "SUBMISSION_UNKNOWN", "provider receipt was reused by a different Matrix event"
        limits = effective_run_limits(
            session,
            tenant_id,
            run_id,
            manifest_limits=manifest.get("limits", {}),
        )
        token_total = input_tokens + output_tokens
        prior_tokens = session.execute(select(func.coalesce(func.sum(usage_record.c.quantity), 0)).where(
            usage_record.c.tenant_id == tenant_id,
            usage_record.c.run_id == run_id,
            usage_record.c.category == "model",
        )).scalar_one()
        prior_calls = session.execute(select(func.coalesce(func.sum(usage_record.c.quantity), 0)).where(
            usage_record.c.tenant_id == tenant_id,
            usage_record.c.run_id == run_id,
            usage_record.c.category == "model_calls",
        )).scalar_one()
        if prior_calls + call_count > int(limits.get("model_calls", 0)):
            return "BUDGET", "model call limit reached"
        if prior_tokens + token_total > int(limits.get("input_tokens", 0)) + int(limits.get("output_tokens", 0)):
            return "BUDGET", "model token limit reached"
        reservation = None
        if cost is not None:
            reservation = session.execute(select(budget_reservation).where(
                budget_reservation.c.tenant_id == tenant_id, budget_reservation.c.run_id == run_id,
                budget_reservation.c.category == "run_total",
            ).with_for_update()).mappings().one()
            if reservation["consumed_amount"] + cost > reservation["limit_amount"]:
                return "BUDGET", "USD 20 hard limit reached or would be exceeded"
        session.execute(usage_record.insert().values(
            id=uuid4(), tenant_id=tenant_id, run_id=run_id, task_id=task_id,
            category="model", quantity=token_total, cost=cost or 0,
            idempotency_key=f"provider:{receipt_id}", created_at=now,
        ))
        session.execute(usage_record.insert().values(
            id=uuid4(), tenant_id=tenant_id, run_id=run_id, task_id=task_id,
            category="model_calls", quantity=call_count, cost=0,
            idempotency_key=f"provider:{receipt_id}:calls", created_at=now,
        ))
        if cost is None:
            session.execute(usage_record.insert().values(
                id=uuid4(), tenant_id=tenant_id, run_id=run_id, task_id=task_id,
                category="model_cost_unavailable", quantity=1, cost=0,
                idempotency_key=f"provider:{receipt_id}:cost-unavailable", created_at=now,
            ))
        else:
            assert reservation is not None
            session.execute(update(budget_reservation).where(
                budget_reservation.c.id == reservation["id"], budget_reservation.c.tenant_id == tenant_id,
            ).values(
                consumed_amount=reservation["consumed_amount"] + cost,
                status="CONSUMED", updated_at=now,
            ))
        return None

    @staticmethod
    def _persist_findings(
        session: Session, tenant_id: UUID, accepted: AcceptedMatrixEvent, now: datetime
    ) -> None:
        handoff = accepted.handoff
        expected_dimension = _DOMAIN_BY_AGENT[handoff.agent_code]
        if handoff.dimension != expected_dimension:
            raise ValueError("specialist handoff dimension does not match its frozen role")
        known = set(session.execute(select(evidence.c.id).where(
            evidence.c.tenant_id == tenant_id, evidence.c.run_id == handoff.run_id,
            evidence.c.id.in_(handoff.evidence_refs or [UUID(int=0)]),
        )).scalars())
        if known != set(handoff.evidence_refs):
            raise ValueError("handoff references Evidence outside the durable Run")
        for claim in handoff.claims:
            if expected_dimension == "GEO_POLICY_TREND" and not all(
                (claim.region, claim.fetched_at, claim.valid_until, claim.trend_signal)
            ):
                raise ValueError("time/region Claims require region, fetched_at, valid_until and trend_signal")
            finding_id = uuid4()
            grade = "INSUFFICIENT_EVIDENCE" if claim.hypothesis else "MODERATE"
            session.execute(finding.insert().values(
                id=finding_id, tenant_id=tenant_id, run_id=handoff.run_id, task_id=handoff.task_id,
                dimension_code=expected_dimension, grade=grade,
                claim_type="HYPOTHESIS" if claim.hypothesis else "FINDING", statement=claim.statement,
                is_hypothesis=claim.hypothesis, submitted_by=handoff.agent_code, submitted_at=now,
                structured_result={
                    "schema": "AgentHandoffV1", "matrix_event_id": accepted.matrix_event_id,
                    "claim": claim.model_dump(mode="json"), "risk": handoff.risk,
                    "confidence": handoff.confidence,
                },
                simulated=False, hard_block=False,
            ))
            for evidence_id in claim.evidence_ids:
                session.execute(finding_evidence.insert().values(
                    tenant_id=tenant_id, finding_id=finding_id, evidence_id=evidence_id,
                    relation_type="SUPPORTS",
                ))

    def _advance(self, session: Session, tenant_id: UUID, run_id: UUID, stage_code: str, now: datetime) -> UUID | None:
        if stage_code == "LEADER_PLANNING":
            self._complete_stage(session, tenant_id, run_id, stage_code, now)
            self._unlock(session, tenant_id, run_id, "DOMAIN_REVIEW", now)
            self._current_stage(session, tenant_id, run_id, "DOMAIN_REVIEW", now)
            enqueue_ready_tasks(session, tenant_id, run_id, "DOMAIN_REVIEW")
        elif stage_code == "DOMAIN_REVIEW" and self._stage_all_succeeded(session, tenant_id, run_id, stage_code):
            self._complete_stage(session, tenant_id, run_id, stage_code, now)
            self._unlock(session, tenant_id, run_id, "EVIDENCE_AUDIT", now)
            self._current_stage(session, tenant_id, run_id, "EVIDENCE_AUDIT", now)
            enqueue_ready_tasks(session, tenant_id, run_id, "EVIDENCE_AUDIT")
        elif stage_code == "EVIDENCE_AUDIT":
            self._complete_stage(session, tenant_id, run_id, stage_code, now)
            self._unlock(session, tenant_id, run_id, "RULE_SYNTHESIS", now)
            self._current_stage(session, tenant_id, run_id, "RULE_SYNTHESIS", now)
            enqueue_ready_tasks(session, tenant_id, run_id, "RULE_SYNTHESIS")
        elif stage_code == "RULE_SYNTHESIS":
            report_id = self._synthesize(session, tenant_id, run_id, now)
            self._complete_stage(session, tenant_id, run_id, stage_code, now)
            self._set_run(session, tenant_id, run_id, "COMPLETED", now, "Rule-owned report committed", None)
            return report_id
        return None

    @staticmethod
    def _stage_all_succeeded(session: Session, tenant_id: UUID, run_id: UUID, code: str) -> bool:
        remaining = session.execute(select(func.count()).select_from(task).where(
            task.c.tenant_id == tenant_id, task.c.run_id == run_id, task.c.stage_code == code,
            task.c.status != "SUCCEEDED",
        )).scalar_one()
        return remaining == 0

    @staticmethod
    def _complete_stage(session: Session, tenant_id: UUID, run_id: UUID, code: str, now: datetime) -> None:
        session.execute(update(stage).where(
            stage.c.tenant_id == tenant_id, stage.c.run_id == run_id, stage.c.code == code,
        ).values(status="COMPLETED", completed_at=now))

    @staticmethod
    def _unlock(session: Session, tenant_id: UUID, run_id: UUID, code: str, now: datetime) -> None:
        session.execute(update(stage).where(
            stage.c.tenant_id == tenant_id, stage.c.run_id == run_id, stage.c.code == code,
        ).values(status="RUNNING", started_at=now))
        session.execute(update(task).where(
            task.c.tenant_id == tenant_id, task.c.run_id == run_id,
            task.c.stage_code == code, task.c.status == "BLOCKED",
        ).values(status="READY", updated_at=now))

    @staticmethod
    def _current_stage(session: Session, tenant_id: UUID, run_id: UUID, code: str, now: datetime) -> None:
        session.execute(update(evaluation_run).where(
            evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id,
        ).values(current_stage=code, updated_at=now))
        session.execute(run_status_history.insert().values(
            id=uuid4(), tenant_id=tenant_id, run_id=run_id, from_status="RUNNING", to_status="RUNNING",
            reason=f"Stage advanced to {code}", occurred_at=now,
        ))

    @staticmethod
    def _audit_findings(
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        proposals: list[AuditResultV1 | AuditResultV2],
        now: datetime,
    ) -> str | None:
        rows = session.execute(select(finding.c.id, finding.c.is_hypothesis).where(
            finding.c.tenant_id == tenant_id, finding.c.run_id == run_id,
        )).all()
        already_audited = set(session.execute(select(evidence_audit.c.finding_id).where(
            evidence_audit.c.tenant_id == tenant_id, evidence_audit.c.run_id == run_id,
        )).scalars())
        rows = [item for item in rows if item[0] not in already_audited]
        proposed = {item.finding_id: item for item in proposals}
        if set(proposed) != {item[0] for item in rows}:
            return "Auditor must return exactly one audit result for every durable Finding"
        run_evidence_ids = set(session.execute(select(evidence.c.id).where(
            evidence.c.tenant_id == tenant_id, evidence.c.run_id == run_id,
        )).scalars())
        for finding_id, hypothesis in rows:
            linked_evidence = session.execute(
                select(
                    evidence.c.id,
                    evidence.c.evidence_level,
                    evidence.c.simulated,
                    evidence.c.valid_until,
                    finding_evidence.c.relation_type,
                )
                .join(
                    finding_evidence,
                    (finding_evidence.c.tenant_id == evidence.c.tenant_id)
                    & (finding_evidence.c.evidence_id == evidence.c.id),
                )
                .where(
                    finding_evidence.c.tenant_id == tenant_id,
                    finding_evidence.c.finding_id == finding_id,
                )
            ).mappings().all()
            count = len(linked_evidence)
            proposal = proposed[finding_id]
            contract_version = "2.0" if isinstance(proposal, AuditResultV2) else "1.0"
            audit_decision: str
            if isinstance(proposal, AuditResultV2):
                if not set(proposal.evidence_ids).issubset(run_evidence_ids):
                    return f"AuditResultV2 references evidence outside Run {run_id}"
                linked_evidence_ids = {item["id"] for item in linked_evidence}
                if set(proposal.evidence_ids) != linked_evidence_ids:
                    return f"AuditResultV2 must cite exactly the Evidence linked to Finding {finding_id}"
                derived_flags: set[str] = set()
                if linked_evidence and all(item["evidence_level"] == "E0" for item in linked_evidence):
                    derived_flags.add("SELF_CLAIM")
                if linked_evidence and all(item["simulated"] for item in linked_evidence):
                    derived_flags.add("SIMULATION_ONLY")
                if linked_evidence and all(
                    item["valid_until"] is not None and item["valid_until"] <= now for item in linked_evidence
                ):
                    derived_flags.add("EXPIRED")
                relations = {item["relation_type"] for item in linked_evidence}
                if {"SUPPORTS", "CONTRADICTS"}.issubset(relations):
                    derived_flags.add("CONFLICT")
                if not derived_flags.issubset(set(proposal.flags)):
                    missing = ", ".join(sorted(derived_flags.difference(proposal.flags)))
                    return f"AuditResultV2 for Finding {finding_id} omits control-plane flags: {missing}"
                required_rule = {
                    "ACCEPTED": "KB-EVD-D01",
                    "DOWNGRADED": "KB-EVD-D02",
                    "REJECTED": "KB-EVD-D03",
                    "NEEDS_MORE": "KB-EVD-D04",
                }[proposal.decision]
                if required_rule not in proposal.rule_ids:
                    return f"AuditResultV2 decision for Finding {finding_id} lacks {required_rule}"
                if count == 0 and proposal.decision != "NEEDS_MORE":
                    return f"Finding {finding_id} without evidence must use NEEDS_MORE"
                if "TAMPERED" in proposal.flags and proposal.decision != "REJECTED":
                    return f"Tampered evidence for Finding {finding_id} must be rejected"
                if (hypothesis or proposal.flags) and proposal.decision == "ACCEPTED":
                    return f"Flagged or hypothetical Finding {finding_id} cannot be accepted"
                audit_decision = proposal.decision
                rule_ids = proposal.rule_ids
                referenced_evidence_ids = [str(value) for value in proposal.evidence_ids]
                score_components = proposal.score_components.model_dump()
                flags = proposal.flags
            else:
                audit_decision = (
                    "NEEDS_MORE_EVIDENCE" if count == 0 else ("DOWNGRADED" if hypothesis else "ACCEPTED")
                )
                if proposal.decision != audit_decision:
                    return f"Auditor result conflicts with evidence policy for Finding {finding_id}"
                rule_ids = []
                referenced_evidence_ids = []
                score_components = {}
                flags = []
            session.execute(evidence_audit.insert().values(
                id=uuid4(), tenant_id=tenant_id, run_id=run_id, finding_id=finding_id,
                decision=audit_decision, auditor_id="evidence-auditor",
                reason=f"{proposal.reason}; control-plane evidence policy verified",
                contract_version=contract_version,
                rule_ids=rule_ids,
                referenced_evidence_ids=referenced_evidence_ids,
                score_components=score_components,
                flags=flags,
                audited_at=now,
            ))
        return None

    def _synthesize(self, session: Session, tenant_id: UUID, run_id: UUID, now: datetime) -> UUID:
        rows = session.execute(select(
            finding.c.id,
            finding.c.dimension_code,
            finding.c.grade,
            evidence_audit.c.decision,
            evidence_audit.c.reason,
            evidence_audit.c.contract_version,
            evidence_audit.c.rule_ids,
            evidence_audit.c.referenced_evidence_ids,
            evidence_audit.c.score_components,
            evidence_audit.c.flags,
        ).join(evidence_audit, (
            evidence_audit.c.tenant_id == finding.c.tenant_id
        ) & (evidence_audit.c.finding_id == finding.c.id)).where(
            finding.c.tenant_id == tenant_id, finding.c.run_id == run_id,
        )).mappings().all()
        grades: dict[str, str] = {}
        blocks: list[str] = []
        finding_ids: list[UUID] = []
        for dimension in _DIMENSIONS:
            candidates = []
            for item in rows:
                finding_id = item["id"]
                audit_decision = item["decision"]
                if item["dimension_code"] != dimension:
                    continue
                rank = _RANK[item["grade"]]
                if audit_decision in {"DOWNGRADED", "NEEDS_MORE", "NEEDS_MORE_EVIDENCE"}:
                    rank = max(0, rank - 1)
                    blocks.append(f"finding_{audit_decision.lower()}:{finding_id}")
                if audit_decision == "REJECTED":
                    blocks.append(f"finding_rejected:{finding_id}")
                    continue
                candidates.append(rank)
                finding_ids.append(finding_id)
            grades[dimension] = _GRADE[min(candidates)] if candidates else "INSUFFICIENT_EVIDENCE"
            if not candidates:
                blocks.append(f"missing_dimension_evidence:{dimension}")
        if any(value.startswith("finding_rejected") for value in blocks):
            recommendation = "PAUSE"
        elif blocks or "INSUFFICIENT_EVIDENCE" in grades.values():
            recommendation = "VALIDATE_FURTHER"
        elif "WEAK" in grades.values():
            recommendation = "ADJUST"
        else:
            recommendation = "PROCEED"
        actions = [
            f"Collect stronger authorized evidence for {dimension.replace('_', ' ').title()}"
            for dimension, grade in grades.items() if grade not in {"STRONG", "MODERATE"}
        ][:3]
        decision_id, report_id = uuid4(), uuid4()
        body = json.dumps({
            "schema": "launchscope.report.v2", "run_id": str(run_id), "recommendation": recommendation,
            "dimension_grades": grades, "blocking_reasons": list(dict.fromkeys(blocks)),
            "action_items": actions,
            "calibration_results": [
                {
                    "finding_id": str(item["id"]),
                    "decision": item["decision"],
                    "reason": item["reason"],
                    "contract_version": item["contract_version"],
                    "rule_ids": item["rule_ids"],
                    "evidence_ids": item["referenced_evidence_ids"],
                    "score_components": item["score_components"],
                    "flags": item["flags"],
                }
                for item in rows
            ],
            "generated_by": "deterministic-rule-layer",
        }, sort_keys=True, ensure_ascii=False).encode("utf-8")
        key = f"tenant/{tenant_id}/run/{run_id}/report/{report_id}.json"
        digest = self._objects.put_private(key, body, "application/json")
        standard = session.execute(select(evaluation_run.c.standard_version).where(
            evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id,
        )).scalar_one()
        session.execute(decision.insert().values(
            id=decision_id, tenant_id=tenant_id, run_id=run_id, recommendation=recommendation,
            standard_version=standard, dimension_grades=grades,
            hard_blocks=list(dict.fromkeys(blocks)), created_at=now,
        ))
        for finding_id in dict.fromkeys(finding_ids):
            session.execute(decision_finding.insert().values(
                tenant_id=tenant_id, decision_id=decision_id, finding_id=finding_id, role="SUPPORTING",
            ))
        session.execute(report.insert().values(
            id=report_id, tenant_id=tenant_id, run_id=run_id, decision_id=decision_id,
            object_key=key, sha256=digest, status="COMMITTED", action_items=actions, created_at=now,
        ))
        return report_id

    @staticmethod
    def _set_run(
        session: Session, tenant_id: UUID, run_id: UUID, status: str, now: datetime,
        reason: str, failure_class: str | None,
    ) -> None:
        reason = _persisted_diagnostic(reason)
        old = session.execute(select(evaluation_run.c.status).where(
            evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id,
        )).scalar_one()
        session.execute(update(evaluation_run).where(
            evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id,
        ).values(status=status, current_stage="COMPLETED" if status == "COMPLETED" else evaluation_run.c.current_stage,
                 last_failure_class=failure_class, attention_reason=reason if failure_class else None, updated_at=now))
        session.execute(run_status_history.insert().values(
            id=uuid4(), tenant_id=tenant_id, run_id=run_id, from_status=old, to_status=status,
            reason=reason, failure_class=failure_class, occurred_at=now,
        ))


__all__ = ["HandoffApplication", "HandoffResult"]
