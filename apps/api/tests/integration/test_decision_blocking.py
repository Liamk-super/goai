"""T9 deterministic rules: a hard block wins over otherwise strong dimensions."""

from __future__ import annotations

from uuid import uuid4

from launchscope_api.modules.decision_report.rule_application import DecisionPolicyError, DecisionRuleApplication
from launchscope_api.modules.decision_report.synthesis_application import SynthesisApplication
from launchscope_domain.aggregates.decision_report import DecisionReport
from launchscope_domain.aggregates.evidence_review import Evidence, EvidenceReview, Finding
from launchscope_domain.enums import (
    ALL_DIMENSIONS,
    DimensionCode,
    EvidenceAuditDecision,
    EvidenceLevel,
    FindingGrade,
    Recommendation,
)
from launchscope_domain.value_objects import TenantScope


def test_hard_block_overrides_four_dimension_strength() -> None:
    scope = TenantScope(uuid4(), uuid4(), uuid4(), uuid4(), uuid4())
    review = EvidenceReview(scope)
    for dimension in ALL_DIMENSIONS:
        evidence_id = uuid4()
        review.add_evidence(
            Evidence.create(
                scope,
                evidence_id=evidence_id,
                object_key=(
                    f"tenant/{scope.tenant_id}/project/{scope.project_id}/version/{scope.product_version_id}/"
                    f"run/{scope.run_id}/evidence/{evidence_id}/source.txt"
                ),
                sha256="b" * 64,
                mime_type="text/plain",
                source_type="MATERIAL",
                trust_level=EvidenceLevel.E4,
                size_bytes=1,
            )
        )
        finding = review.submit_finding(
            Finding.create(
                scope,
                DimensionCode(dimension),
                FindingGrade.STRONG,
                f"{dimension} evidence",
                evidence_ids=(evidence_id,),
                submitted_by="agent",
                hard_block=dimension is DimensionCode.GEO_POLICY_TREND,
                block_reason="hard_block:policy_prohibits_launch"
                if dimension is DimensionCode.GEO_POLICY_TREND
                else None,
            )
        )
        review.audit_finding(finding.finding_id, EvidenceAuditDecision.ACCEPTED, auditor_id="auditor")

    result = DecisionRuleApplication().evaluate(review, standard_version="1.0")
    assert result.recommendation is Recommendation.PAUSE
    assert "hard_block:policy_prohibits_launch" in result.blocking_reasons
    synthesis = SynthesisApplication().synthesize(
        DecisionReport(scope, "1.0"), result, review=review, action_items=("resolve policy constraint",)
    )
    assert len(synthesis.rendered.evidence_chain) == 4
    assert synthesis.rendered.summary["largest_risk"] == "hard_block:policy_prohibits_launch"
    assert "largest_opportunity" in synthesis.rendered.summary


def test_decision_cannot_bypass_independent_audit() -> None:
    scope = TenantScope(uuid4(), uuid4(), uuid4(), uuid4(), uuid4())
    review = EvidenceReview(scope)
    review.submit_finding(Finding.hypothesis(scope, DimensionCode.USER_USAGE, "unreviewed", submitted_by="agent"))

    try:
        DecisionRuleApplication().evaluate(review, standard_version="1.0")
    except DecisionPolicyError:
        pass
    else:
        raise AssertionError("rule evaluation allowed an unaudited Finding")
