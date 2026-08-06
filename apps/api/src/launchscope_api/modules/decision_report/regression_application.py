"""Same-standard V1/V2 verification; standards never silently drift."""

from __future__ import annotations

from dataclasses import dataclass

from launchscope_domain.aggregates.decision_report import Decision
from launchscope_domain.enums import RegressionResult
from launchscope_domain.value_objects import TenantScope


@dataclass(frozen=True, slots=True)
class VerificationRun:
    scope: TenantScope
    standard_version: str
    core_task_refs: tuple[str, ...]
    decision: Decision
    resolved_issues: tuple[str, ...] = ()
    remaining_failures: tuple[str, ...] = ()
    confirmed_hypotheses: tuple[str, ...] = ()
    refuted_hypotheses: tuple[str, ...] = ()
    new_risks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VersionRegression:
    result: RegressionResult
    comparable: bool
    standard_version: str
    resolved_issues: tuple[str, ...]
    remaining_failures: tuple[str, ...]
    confirmed_hypotheses: tuple[str, ...]
    refuted_hypotheses: tuple[str, ...]
    new_risks: tuple[str, ...]
    recommendation_changed: bool
    supplemental_standard_version: str | None = None


class VersionRegressionApplication:
    def compare(self, baseline: VerificationRun, candidate: VerificationRun) -> VersionRegression:
        if (
            baseline.scope.tenant_id != candidate.scope.tenant_id
            or baseline.scope.project_id != candidate.scope.project_id
        ):
            raise ValueError("V1/V2 verification must use the same tenant and project")
        if baseline.standard_version != candidate.standard_version:
            return VersionRegression(
                result=RegressionResult.INSUFFICIENT_EVIDENCE,
                comparable=False,
                standard_version=baseline.standard_version,
                resolved_issues=(),
                remaining_failures=(),
                confirmed_hypotheses=(),
                refuted_hypotheses=(),
                new_risks=(),
                recommendation_changed=False,
                supplemental_standard_version=candidate.standard_version,
            )
        if baseline.core_task_refs != candidate.core_task_refs:
            raise ValueError("V1/V2 verification must reuse the frozen core task set")
        failed = bool(candidate.remaining_failures or candidate.decision.blocking_reasons)
        return VersionRegression(
            result=RegressionResult.FAIL if failed else RegressionResult.PASS,
            comparable=True,
            standard_version=baseline.standard_version,
            resolved_issues=candidate.resolved_issues,
            remaining_failures=candidate.remaining_failures,
            confirmed_hypotheses=candidate.confirmed_hypotheses,
            refuted_hypotheses=candidate.refuted_hypotheses,
            new_risks=candidate.new_risks,
            recommendation_changed=baseline.decision.recommendation != candidate.decision.recommendation,
        )


__all__ = ["VerificationRun", "VersionRegression", "VersionRegressionApplication"]
