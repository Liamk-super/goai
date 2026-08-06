"""T9 V1/V2 comparison keeps comparable and changed-standard results separate."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from launchscope_api.modules.decision_report.regression_application import VerificationRun, VersionRegressionApplication
from launchscope_domain.aggregates.decision_report import Decision
from launchscope_domain.enums import ALL_DIMENSIONS, FindingGrade, Recommendation, RegressionResult
from launchscope_domain.value_objects import TenantScope


def _decision(scope: TenantScope, recommendation: Recommendation) -> Decision:
    return Decision(
        decision_id=uuid4(),
        scope=scope,
        run_id=scope.run_id,
        standard_version="1.0",
        recommendation=recommendation,
        dimension_grades={dimension: FindingGrade.MODERATE for dimension in ALL_DIMENSIONS},
        finding_ids=(uuid4(),),
        created_at=datetime.now(UTC),
    )


def test_version_regression_reuses_same_project_standard_and_core_tasks() -> None:
    project_ids = (uuid4(), uuid4(), uuid4())
    baseline_scope = TenantScope(project_ids[0], project_ids[1], project_ids[2], uuid4(), uuid4())
    candidate_scope = TenantScope(project_ids[0], project_ids[1], project_ids[2], uuid4(), uuid4())
    baseline = VerificationRun(baseline_scope, "1.0", ("core-a",), _decision(baseline_scope, Recommendation.ADJUST))
    candidate = VerificationRun(
        candidate_scope,
        "1.0",
        ("core-a",),
        _decision(candidate_scope, Recommendation.PROCEED),
        resolved_issues=("checkout latency",),
        confirmed_hypotheses=("activation funnel",),
    )
    result = VersionRegressionApplication().compare(baseline, candidate)

    assert result.result is RegressionResult.PASS
    assert result.comparable
    assert result.recommendation_changed
    assert result.resolved_issues == ("checkout latency",)


def test_changed_standard_is_supplemental_not_a_silent_comparison() -> None:
    scope = TenantScope(uuid4(), uuid4(), uuid4(), uuid4(), uuid4())
    baseline = VerificationRun(scope, "1.0", ("core-a",), _decision(scope, Recommendation.ADJUST))
    candidate = VerificationRun(scope.with_run(uuid4()), "2.0", ("core-a",), _decision(scope, Recommendation.ADJUST))
    result = VersionRegressionApplication().compare(baseline, candidate)

    assert result.result is RegressionResult.INSUFFICIENT_EVIDENCE
    assert not result.comparable
    assert result.supplemental_standard_version == "2.0"
