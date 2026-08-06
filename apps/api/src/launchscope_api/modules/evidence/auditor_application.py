"""Independent, append-only auditing for evidence-backed findings.

The auditor does not amend a Finding.  It records a later audit decision, so
the original Agent output remains traceable even when it is unusable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from launchscope_domain.aggregates.evidence_review import EvidenceReview, Finding
from launchscope_domain.enums import EvidenceAuditDecision, EvidenceLevel


@dataclass(frozen=True, slots=True)
class AuditResult:
    finding_id: UUID
    decision: EvidenceAuditDecision
    reasons: tuple[str, ...]


class EvidenceAuditorApplication:
    """Apply the frozen evidence policy before a Finding can inform a decision."""

    def audit(
        self,
        review: EvidenceReview,
        finding_id: UUID,
        *,
        auditor_id: str,
        region: str | None,
        at: datetime | None = None,
    ) -> AuditResult:
        finding = review.findings.get(finding_id)
        if finding is None:
            raise ValueError("finding was not found in this EvidenceReview")
        moment = at.astimezone(UTC) if at is not None else datetime.now(UTC)
        decision, reasons = self._decide(review, finding, region=region, at=moment)
        review.audit_finding(finding_id, decision, auditor_id=auditor_id, reason="; ".join(reasons))
        return AuditResult(finding_id=finding_id, decision=decision, reasons=reasons)

    @staticmethod
    def _decide(
        review: EvidenceReview,
        finding: Finding,
        *,
        region: str | None,
        at: datetime,
    ) -> tuple[EvidenceAuditDecision, tuple[str, ...]]:
        if finding.scope != review.scope:
            return EvidenceAuditDecision.REJECTED, ("finding_scope_violation",)
        if not finding.evidence_ids:
            return EvidenceAuditDecision.NEEDS_MORE_EVIDENCE, ("finding_without_evidence",)

        evidence_items = [review.evidence.get(evidence_id) for evidence_id in finding.evidence_ids]
        if any(item is None for item in evidence_items):
            return EvidenceAuditDecision.REJECTED, ("evidence_reference_not_in_review",)
        evidence = tuple(item for item in evidence_items if item is not None)
        if any(item.scope != review.scope for item in evidence):
            return EvidenceAuditDecision.REJECTED, ("evidence_scope_violation",)
        if any(not item.time_scope.is_applicable(at=at, region=region) for item in evidence):
            return EvidenceAuditDecision.REJECTED, ("evidence_expired_or_region_unauthorized",)
        if any(not item.ref.object_key or not item.ref.sha256 for item in evidence):
            return EvidenceAuditDecision.REJECTED, ("evidence_metadata_incomplete",)
        if any(finding.finding_id in conflict.finding_ids for conflict in review.unresolved_conflicts):
            return EvidenceAuditDecision.NEEDS_MORE_EVIDENCE, ("unresolved_finding_conflict",)
        if finding.simulated or any(item.simulated for item in evidence):
            return EvidenceAuditDecision.DOWNGRADED, ("simulated_evidence_cannot_prove_real_world_claim",)
        if any(EvidenceLevel(item.ref.trust_level) in {EvidenceLevel.E0, EvidenceLevel.E1} for item in evidence):
            return EvidenceAuditDecision.DOWNGRADED, ("low_credibility_evidence",)
        return EvidenceAuditDecision.ACCEPTED, ("evidence_chain_verified",)


__all__ = ["AuditResult", "EvidenceAuditorApplication"]
