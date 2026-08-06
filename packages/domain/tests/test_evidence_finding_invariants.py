from __future__ import annotations

from uuid import uuid4

import pytest

from launchscope_domain import (
    DimensionCode,
    Evidence,
    EvidenceAuditDecision,
    EvidenceReview,
    Finding,
    FindingGrade,
    MissingEvidenceError,
    TenantScopeViolation,
)


def test_finding_without_evidence_must_be_hypothesis(scope) -> None:
    review = EvidenceReview(scope)
    finding = Finding.create(
        scope,
        DimensionCode.USER_USAGE,
        FindingGrade.STRONG,
        "unsupported claim",
        submitted_by="agent",
    )
    downgraded = review.submit_finding(finding)
    assert downgraded.is_hypothesis is True
    assert downgraded.grade is FindingGrade.INSUFFICIENT_EVIDENCE

    hypothesis = Finding.hypothesis(scope, DimensionCode.USER_USAGE, "needs validation", submitted_by="agent")
    review.submit_finding(hypothesis)
    assert review.finding_items == (downgraded, hypothesis)


def test_evidence_and_finding_references_are_tenant_and_run_scoped(scope, other_scope) -> None:
    review = EvidenceReview(scope)
    evidence = Evidence.create(
        scope,
        object_key="tenant/project/evidence.bin",
        sha256="a" * 64,
        mime_type="application/octet-stream",
        source_type="MATERIAL",
        trust_level="E3",
    )
    review.add_evidence(evidence)
    with pytest.raises(TenantScopeViolation):
        review.add_evidence(
            Evidence.create(
                other_scope.with_product_version(uuid4()).with_run(uuid4()),
                object_key="other/evidence.bin",
                sha256="b" * 64,
                mime_type="application/octet-stream",
                source_type="MATERIAL",
                trust_level="E3",
            )
        )

    missing = Finding.create(
        scope,
        DimensionCode.PRODUCT_IMPLEMENTATION,
        FindingGrade.MODERATE,
        "missing reference",
        evidence_ids=(uuid4(),),
    )
    with pytest.raises(MissingEvidenceError):
        review.submit_finding(missing)


def test_auditor_appends_result_without_rewriting_original_finding(scope) -> None:
    review = EvidenceReview(scope)
    evidence = Evidence.create(
        scope,
        object_key="tenant/project/evidence.txt",
        sha256="c" * 64,
        mime_type="text/plain",
        source_type="MATERIAL",
        trust_level="E3",
    )
    review.add_evidence(evidence)
    finding = Finding.create(
        scope,
        DimensionCode.PRODUCT_IMPLEMENTATION,
        FindingGrade.STRONG,
        "supported claim",
        evidence_ids=(evidence.evidence_id,),
        submitted_by="agent",
    )
    review.submit_finding(finding)
    audit = review.audit_finding(finding.finding_id, EvidenceAuditDecision.DOWNGRADED, auditor_id="auditor")
    assert audit.decision is EvidenceAuditDecision.DOWNGRADED
    assert review.findings[finding.finding_id].grade is FindingGrade.STRONG
