from __future__ import annotations

from uuid import UUID

import pytest

from launchscope_domain import (
    AppendOnlyViolation,
    DecisionReport,
    DimensionCode,
    Evidence,
    EvidenceReview,
    Finding,
    FindingGrade,
    MaterialMetadata,
    ProductVersion,
    ProjectDossier,
    RuleEvaluator,
    TenantScope,
)


def test_project_dossier_preserves_version_and_profile_history() -> None:
    tenant_id = UUID("10000000-0000-4000-8000-000000000001")
    workspace_id = UUID("20000000-0000-4000-8000-000000000001")
    project_id = UUID("30000000-0000-4000-8000-000000000001")
    dossier_scope = TenantScope(tenant_id, workspace_id, project_id)
    material = MaterialMetadata(
        material_id=UUID("60000000-0000-4000-8000-000000000001"),
        scope=dossier_scope,
        object_key="tenant/project/material.txt",
        sha256="a" * 64,
        mime_type="text/plain",
    )
    dossier = ProjectDossier.create(dossier_scope, "Example product")
    dossier.add_material(material)
    version_scope = dossier_scope.with_product_version(UUID("40000000-0000-4000-8000-000000000001"))
    version = ProductVersion(
        product_version_id=version_scope.product_version_id,
        project_id=project_id,
        label="V1",
        material_ids=(material.material_id,),
        scope=version_scope,
    )
    dossier.add_product_version(version).submit("owner")
    profile = dossier.confirm_profile(version.product_version_id, {"name": "Example"}, "owner")
    assert dossier.current_version is version
    assert version.confirmed_profile is profile
    with pytest.raises(AppendOnlyViolation):
        dossier.confirm_profile(version.product_version_id, {"name": "Overwrite"}, "owner")


def test_decision_report_is_rule_backed_and_append_only(scope) -> None:
    review = EvidenceReview(scope)
    finding_ids = []
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
            FindingGrade.MODERATE,
            f"finding-{index}",
            evidence_ids=(evidence.evidence_id,),
            submitted_by=f"agent-{index}",
        )
        review.submit_finding(finding)
        finding_ids.append(finding.finding_id)
    evaluation = RuleEvaluator().evaluate(review)
    report_store = DecisionReport(scope, "1.0")
    decision, report = report_store.synthesize(evaluation, explanation="rule-backed explanation")
    report_store.commit_report(report.report_id)
    assert decision.finding_ids == tuple(finding_ids)
    with pytest.raises(AppendOnlyViolation):
        report_store.synthesize(evaluation, explanation="overwrite")

