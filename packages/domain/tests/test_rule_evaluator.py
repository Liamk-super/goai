from __future__ import annotations

from launchscope_domain import (
    DimensionCode,
    Evidence,
    EvidenceReview,
    Finding,
    FindingGrade,
    Recommendation,
    RuleEvaluator,
)


def test_rule_evaluator_is_conservative_and_does_not_average(scope) -> None:
    review = EvidenceReview(scope)
    findings = []
    for index, dimension in enumerate(DimensionCode):
        evidence = Evidence.create(
            scope,
            object_key=f"tenant/project/evidence-{index}.txt",
            sha256=(hex(index + 10)[2:] * 64)[:64],
            mime_type="text/plain",
            source_type="MATERIAL",
            trust_level="E3",
        )
        review.add_evidence(evidence)
        finding = Finding.create(
            scope,
            dimension,
            FindingGrade.STRONG if index else FindingGrade.INSUFFICIENT_EVIDENCE,
            f"finding-{index}",
            evidence_ids=(evidence.evidence_id,),
            submitted_by=f"agent-{index}",
        )
        review.submit_finding(finding)
        findings.append(finding)

    result = RuleEvaluator().evaluate(review)
    assert result.dimension_grades[DimensionCode.PRODUCT_IMPLEMENTATION] is FindingGrade.INSUFFICIENT_EVIDENCE
    assert result.recommendation is Recommendation.VALIDATE_FURTHER
    assert all(dimension in result.dimension_grades for dimension in DimensionCode)
