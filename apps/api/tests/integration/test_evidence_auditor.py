"""T9 audit policy: unsupported, stale, conflicting and simulated claims fail closed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from launchscope_api.modules.evidence.auditor_application import EvidenceAuditorApplication
from launchscope_domain.aggregates.evidence_review import Evidence, EvidenceReview, Finding
from launchscope_domain.enums import DimensionCode, EvidenceAuditDecision, EvidenceLevel, FindingGrade
from launchscope_domain.value_objects import TenantScope, TimeScope


def _scope() -> TenantScope:
    return TenantScope(uuid4(), uuid4(), uuid4(), uuid4(), uuid4())


def _evidence(scope: TenantScope, *, valid_until: datetime | None = None, simulated: bool = False) -> Evidence:
    evidence_id = uuid4()
    return Evidence.create(
        scope,
        evidence_id=evidence_id,
        object_key=(
            f"tenant/{scope.tenant_id}/project/{scope.project_id}/version/{scope.product_version_id}/"
            f"run/{scope.run_id}/evidence/{evidence_id}/source.txt"
        ),
        sha256="a" * 64,
        mime_type="text/plain",
        source_type="PUBLIC_RESEARCH",
        trust_level=EvidenceLevel.E3,
        size_bytes=1,
        time_scope=TimeScope(region="CN", valid_until=valid_until),
        simulated=simulated,
    )


def test_auditor_rejects_expired_and_degrades_unsupported_findings() -> None:
    scope = _scope()
    review = EvidenceReview(scope)
    expired = _evidence(scope, valid_until=datetime.now(UTC) - timedelta(seconds=1))
    review.add_evidence(expired)
    stale_finding = review.submit_finding(
        Finding.create(
            scope,
            DimensionCode.USER_USAGE,
            FindingGrade.STRONG,
            "stale claim",
            evidence_ids=(expired.evidence_id,),
            submitted_by="agent",
        )
    )
    hypothesis = review.submit_finding(
        Finding.hypothesis(scope, DimensionCode.USER_USAGE, "unproven claim", submitted_by="agent")
    )
    auditor = EvidenceAuditorApplication()

    assert (
        auditor.audit(review, stale_finding.finding_id, auditor_id="auditor", region="CN").decision
        is EvidenceAuditDecision.REJECTED
    )
    assert (
        auditor.audit(review, hypothesis.finding_id, auditor_id="auditor", region="CN").decision
        is EvidenceAuditDecision.NEEDS_MORE_EVIDENCE
    )


def test_auditor_degrades_simulated_evidence_without_overwriting_finding() -> None:
    scope = _scope()
    review = EvidenceReview(scope)
    evidence = _evidence(scope, simulated=True)
    review.add_evidence(evidence)
    finding = review.submit_finding(
        Finding.create(
            scope,
            DimensionCode.USER_USAGE,
            FindingGrade.STRONG,
            "simulated user opinion",
            evidence_ids=(evidence.evidence_id,),
            submitted_by="agent",
        )
    )
    result = EvidenceAuditorApplication().audit(review, finding.finding_id, auditor_id="auditor", region="CN")

    assert result.decision is EvidenceAuditDecision.DOWNGRADED
    assert review.findings[finding.finding_id].grade is FindingGrade.STRONG
