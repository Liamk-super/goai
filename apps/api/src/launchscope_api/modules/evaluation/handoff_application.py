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
    MatrixSenderDirectory,
)

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
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._directory = directory
        self._bridge = AgentTeamsBridge()

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
            accepted = self._bridge.accept_matrix_event(
                raw_event, self._directory, expected_run_id=run_id, expected_task_id=task_id
            )
            prior = session.execute(
                select(matrix_event_receipt.c.payload_sha256).where(
                    matrix_event_receipt.c.tenant_id == actor.tenant_id,
                    matrix_event_receipt.c.matrix_event_id == accepted.matrix_event_id,
                )
            ).scalar_one_or_none()
            if prior is not None:
                if prior != accepted.payload_sha256:
                    raise ValueError("Matrix event ID was replayed with a different payload")
                status = session.execute(select(evaluation_run.c.status).where(
                    evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id
                )).scalar_one()
                return HandoffResult(accepted.matrix_event_id, assigned["status"], status, duplicate=True)

            expected_agent = str(assigned["agent_identity_ref"]).split("@", 1)[0]
            if accepted.handoff.agent_code != expected_agent:
                raise ValueError("handoff Agent does not own the durable Task")
            session.execute(matrix_event_receipt.insert().values(
                id=uuid4(), tenant_id=actor.tenant_id, run_id=run_id, task_id=task_id,
                room_id=accepted.room_id, matrix_event_id=accepted.matrix_event_id,
                sender_mxid=accepted.sender_mxid, payload_sha256=accepted.payload_sha256,
                processing_status="PROCESSED", created_at=now,
            ))
            handoff = accepted.handoff
            session.execute(matrix_handoff.insert().values(
                id=uuid4(), tenant_id=actor.tenant_id, run_id=run_id, task_id=task_id,
                room_id=accepted.room_id, sender_agent=handoff.agent_code,
                receiver_agent="evaluation-manager", kind="RESULT", finding_id=None,
                evidence_ids=[str(value) for value in handoff.evidence_refs], risk=handoff.risk,
                confidence=Decimal(str(handoff.confidence)), approval_required=handoff.needs_human_approval,
                payload_sha256=accepted.payload_sha256, created_at=now,
            ))
            usage_failure = self._record_provider_usage(
                session, actor.tenant_id, run_id, task_id, raw_event, now
            )
            if usage_failure is not None:
                failure, reason = usage_failure
                session.execute(update(task).where(task.c.id == task_id).values(
                    status="NEEDS_ATTENTION", last_failure_class=failure, last_error=reason, updated_at=now,
                ))
                self._set_run(session, actor.tenant_id, run_id, "NEEDS_ATTENTION", now, reason, failure)
                return HandoffResult(accepted.matrix_event_id, "NEEDS_ATTENTION", "NEEDS_ATTENTION")
            if handoff.status != "SUCCEEDED":
                failure = handoff.failure_class or "SUBMISSION_UNKNOWN"
                session.execute(update(task).where(task.c.id == task_id).values(
                    status="NEEDS_ATTENTION", last_failure_class=failure,
                    last_error=handoff.next_action, updated_at=now,
                ))
                self._set_run(session, actor.tenant_id, run_id, "NEEDS_ATTENTION", now,
                              f"Task result requires attention: {handoff.next_action}", failure)
                return HandoffResult(accepted.matrix_event_id, "NEEDS_ATTENTION", "NEEDS_ATTENTION")

            if handoff.agent_code == "evidence-auditor":
                audit_failure = self._audit_findings(
                    session, actor.tenant_id, run_id, handoff.audit_results, now
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
            session.execute(update(task).where(task.c.id == task_id).values(status="SUCCEEDED", updated_at=now))
            report_id = self._advance(session, actor.tenant_id, run_id, assigned["stage_code"], now)
            run_status = session.execute(select(evaluation_run.c.status).where(
                evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id
            )).scalar_one()
            return HandoffResult(accepted.matrix_event_id, "SUCCEEDED", run_status, report_id=report_id)

    @staticmethod
    def _record_provider_usage(
        session: Session, tenant_id: UUID, run_id: UUID, task_id: UUID,
        raw_event: dict[str, object], now: datetime,
    ) -> tuple[str, str] | None:
        content = raw_event.get("content")
        usage = content.get("provider_usage") if isinstance(content, dict) else None
        if (
            not isinstance(usage, dict)
            or usage.get("submission_known") is not True
            or usage.get("usage_known") is not True
        ):
            return "SUBMISSION_UNKNOWN", "model submission or usage state is unknown; automatic retry prohibited"
        try:
            input_tokens = int(usage["input_tokens"])
            output_tokens = int(usage["output_tokens"])
            cost = Decimal(str(usage["cost_usd"]))
            receipt_id = str(usage["receipt_id"]).strip()
        except (KeyError, TypeError, ValueError):
            return "SUBMISSION_UNKNOWN", "provider usage receipt is incomplete; automatic retry prohibited"
        if input_tokens < 0 or output_tokens < 0 or cost < 0 or not receipt_id:
            return "SUBMISSION_UNKNOWN", "provider usage receipt is invalid; automatic retry prohibited"
        manifest = session.execute(select(run_manifest.c.frozen_config).where(
            run_manifest.c.tenant_id == tenant_id, run_manifest.c.run_id == run_id,
        )).scalar_one()
        limits = manifest.get("limits", {})
        totals = session.execute(select(
            func.count(usage_record.c.id), func.coalesce(func.sum(usage_record.c.quantity), 0),
        ).where(usage_record.c.tenant_id == tenant_id, usage_record.c.run_id == run_id)).one()
        if totals[0] + 1 > int(limits.get("model_calls", 0)):
            return "BUDGET", "model call limit reached"
        token_total = input_tokens + output_tokens
        if totals[1] + token_total > int(limits.get("input_tokens", 0)) + int(limits.get("output_tokens", 0)):
            return "BUDGET", "model token limit reached"
        reservation = session.execute(select(budget_reservation).where(
            budget_reservation.c.tenant_id == tenant_id, budget_reservation.c.run_id == run_id,
            budget_reservation.c.category == "run_total",
        ).with_for_update()).mappings().one()
        if reservation["consumed_amount"] + cost > reservation["limit_amount"]:
            return "BUDGET", "USD 20 hard limit reached or would be exceeded"
        duplicate = session.execute(select(usage_record.c.id).where(
            usage_record.c.tenant_id == tenant_id,
            usage_record.c.idempotency_key == f"provider:{receipt_id}",
        )).scalar_one_or_none()
        if duplicate is not None:
            return "SUBMISSION_UNKNOWN", "provider receipt was reused by a different Matrix event"
        session.execute(usage_record.insert().values(
            id=uuid4(), tenant_id=tenant_id, run_id=run_id, task_id=task_id,
            category="model", quantity=token_total, cost=cost,
            idempotency_key=f"provider:{receipt_id}", created_at=now,
        ))
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
        elif stage_code == "DOMAIN_REVIEW" and self._stage_all_succeeded(session, tenant_id, run_id, stage_code):
            self._complete_stage(session, tenant_id, run_id, stage_code, now)
            self._unlock(session, tenant_id, run_id, "EVIDENCE_AUDIT", now)
            self._current_stage(session, tenant_id, run_id, "EVIDENCE_AUDIT", now)
        elif stage_code == "EVIDENCE_AUDIT":
            self._complete_stage(session, tenant_id, run_id, stage_code, now)
            self._unlock(session, tenant_id, run_id, "RULE_SYNTHESIS", now)
            self._current_stage(session, tenant_id, run_id, "RULE_SYNTHESIS", now)
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
        session: Session, tenant_id: UUID, run_id: UUID, proposals: list[AuditResultV1], now: datetime
    ) -> str | None:
        rows = session.execute(select(finding.c.id, finding.c.is_hypothesis).where(
            finding.c.tenant_id == tenant_id, finding.c.run_id == run_id,
        )).all()
        proposed = {item.finding_id: item for item in proposals}
        if set(proposed) != {item[0] for item in rows}:
            return "Auditor must return exactly one audit result for every durable Finding"
        for finding_id, hypothesis in rows:
            count = session.execute(select(func.count()).select_from(finding_evidence).where(
                finding_evidence.c.tenant_id == tenant_id, finding_evidence.c.finding_id == finding_id,
            )).scalar_one()
            audit_decision = "NEEDS_MORE_EVIDENCE" if count == 0 else ("DOWNGRADED" if hypothesis else "ACCEPTED")
            if proposed[finding_id].decision != audit_decision:
                return f"Auditor result conflicts with evidence policy for Finding {finding_id}"
            session.execute(evidence_audit.insert().values(
                id=uuid4(), tenant_id=tenant_id, run_id=run_id, finding_id=finding_id,
                decision=audit_decision, auditor_id="evidence-auditor",
                reason=f"{proposed[finding_id].reason}; control-plane evidence policy verified",
                audited_at=now,
            ))
        return None

    def _synthesize(self, session: Session, tenant_id: UUID, run_id: UUID, now: datetime) -> UUID:
        rows = session.execute(select(
            finding.c.id, finding.c.dimension_code, finding.c.grade, evidence_audit.c.decision,
        ).join(evidence_audit, (
            evidence_audit.c.tenant_id == finding.c.tenant_id
        ) & (evidence_audit.c.finding_id == finding.c.id)).where(
            finding.c.tenant_id == tenant_id, finding.c.run_id == run_id,
        )).all()
        grades: dict[str, str] = {}
        blocks: list[str] = []
        finding_ids: list[UUID] = []
        for dimension in _DIMENSIONS:
            candidates = []
            for finding_id, finding_dimension, grade, audit_decision in rows:
                if finding_dimension != dimension:
                    continue
                rank = _RANK[grade]
                if audit_decision in {"DOWNGRADED", "NEEDS_MORE_EVIDENCE"}:
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
            "action_items": actions, "generated_by": "deterministic-rule-layer",
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
