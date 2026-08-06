"""Application boundary that exposes deterministic four-dimension decisions."""

from __future__ import annotations

from launchscope_domain.aggregates.evidence_review import EvidenceReview
from launchscope_domain.services.rule_evaluator import RuleEvaluation, RuleEvaluator


class DecisionPolicyError(ValueError):
    """A decision was requested before independent evidence review finished."""


class DecisionRuleApplication:
    """Models may describe an evaluation, never select or override it."""

    def __init__(self, evaluator: RuleEvaluator | None = None) -> None:
        self.evaluator = evaluator or RuleEvaluator()

    def evaluate(self, review: EvidenceReview, *, standard_version: str) -> RuleEvaluation:
        unaudited = [
            str(finding.finding_id)
            for finding in review.finding_items
            if review.latest_audit(finding.finding_id) is None
        ]
        if unaudited:
            raise DecisionPolicyError(
                "rule evaluation requires an independent audit for every finding: " + ", ".join(unaudited)
            )
        return self.evaluator.evaluate(review, standard_version=standard_version)


__all__ = ["DecisionPolicyError", "DecisionRuleApplication"]
