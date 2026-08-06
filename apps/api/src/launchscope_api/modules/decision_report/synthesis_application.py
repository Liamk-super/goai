"""Synthesize a DecisionReport from audited rule output only."""

from __future__ import annotations

from dataclasses import dataclass

from launchscope_domain.aggregates.decision_report import Decision, DecisionReport, Report
from launchscope_domain.aggregates.evidence_review import EvidenceReview
from launchscope_domain.services.rule_evaluator import RuleEvaluation

from .report_renderer import RenderedReport, ReportRenderer


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    decision: Decision
    report: Report
    rendered: RenderedReport


class SynthesisApplication:
    """Produces a readable explanation without permitting a model override."""

    def __init__(self, renderer: ReportRenderer | None = None) -> None:
        self.renderer = renderer or ReportRenderer()

    def synthesize(
        self,
        aggregate: DecisionReport,
        evaluation: RuleEvaluation,
        *,
        review: EvidenceReview,
        action_items: tuple[str, ...] = (),
    ) -> SynthesisResult:
        explanation = self._explanation(evaluation)
        decision, report = aggregate.synthesize(
            evaluation,
            explanation=explanation,
            action_items=action_items,
            supersedes_decision_id=(aggregate.current_decision.decision_id if aggregate.current_decision else None),
            supersedes_report_id=(aggregate.current_report.report_id if aggregate.current_report else None),
        )
        return SynthesisResult(
            decision=decision, report=report, rendered=self.renderer.render(report, decision, review)
        )

    @staticmethod
    def _explanation(evaluation: RuleEvaluation) -> str:
        grades = ", ".join(f"{key.value}={value.value}" for key, value in evaluation.dimension_grades.items())
        blocks = ", ".join(evaluation.blocking_reasons) or "none"
        return f"Rule result: {evaluation.recommendation.value}. Dimensions: {grades}. Blocks: {blocks}."


__all__ = ["SynthesisApplication", "SynthesisResult"]
