"""Deterministic rule evaluation for the four decision dimensions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from ..aggregates.evidence_review import EvidenceReview, Finding
from ..enums import (
    ALL_DIMENSIONS,
    DimensionCode,
    EvidenceAuditDecision,
    FindingGrade,
    Recommendation,
)

_GRADE_RANK: dict[FindingGrade, int] = {
    FindingGrade.INSUFFICIENT_EVIDENCE: 0,
    FindingGrade.WEAK: 1,
    FindingGrade.MODERATE: 2,
    FindingGrade.STRONG: 3,
}
_RANK_GRADE: dict[int, FindingGrade] = {
    0: FindingGrade.INSUFFICIENT_EVIDENCE,
    1: FindingGrade.WEAK,
    2: FindingGrade.MODERATE,
    3: FindingGrade.STRONG,
}


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    """The complete, deterministic output of the rule layer."""

    standard_version: str
    dimension_grades: dict[DimensionCode, FindingGrade]
    recommendation: Recommendation
    blocking_reasons: tuple[str, ...]
    finding_ids: tuple[UUID, ...]
    evidence_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        normalized = {DimensionCode(key): FindingGrade(value) for key, value in self.dimension_grades.items()}
        object.__setattr__(self, "dimension_grades", MappingProxyType(normalized))
        object.__setattr__(self, "recommendation", Recommendation(self.recommendation))
        object.__setattr__(self, "blocking_reasons", tuple(self.blocking_reasons))
        object.__setattr__(self, "finding_ids", tuple(UUID(str(value)) for value in self.finding_ids))
        object.__setattr__(self, "evidence_ids", tuple(UUID(str(value)) for value in self.evidence_ids))


class RuleEvaluator:
    """Conservative rule evaluator; it never computes a four-way average."""

    def evaluate(
        self,
        findings: Iterable[Finding] | EvidenceReview,
        *,
        standard_version: str = "1.0",
        unresolved_conflicts: Iterable[object] = (),
    ) -> RuleEvaluation:
        if isinstance(findings, EvidenceReview):
            review = findings
            finding_items = review.finding_items
            conflicts: tuple[object, ...] = tuple(review.unresolved_conflicts)
        else:
            finding_items = tuple(findings)
            conflicts = tuple(unresolved_conflicts)

        accepted: list[Finding] = []
        blocking: list[str] = []
        evidence_ids: set[UUID] = set()
        for finding in finding_items:
            audit = review.latest_audit(finding.finding_id) if isinstance(findings, EvidenceReview) else None
            decision = audit.decision if audit is not None else EvidenceAuditDecision.ACCEPTED
            if decision is EvidenceAuditDecision.REJECTED:
                blocking.append(f"finding_rejected:{finding.finding_id}")
                continue
            if decision is EvidenceAuditDecision.NEEDS_MORE_EVIDENCE:
                blocking.append(f"finding_needs_more_evidence:{finding.finding_id}")
            if not finding.evidence_ids:
                blocking.append(f"finding_without_evidence:{finding.finding_id}")
            if finding.hard_block:
                blocking.append(finding.block_reason or f"hard_block:{finding.finding_id}")
            if decision is EvidenceAuditDecision.DOWNGRADED:
                blocking.append(f"finding_downgraded:{finding.finding_id}")
            accepted.append(finding)
            evidence_ids.update(finding.evidence_ids)

        for conflict in conflicts:
            blocking.append(f"unresolved_conflict:{getattr(conflict, 'conflict_id', conflict)}")

        dimension_grades: dict[DimensionCode, FindingGrade] = {}
        for dimension in ALL_DIMENSIONS:
            dimension_findings = [finding for finding in accepted if finding.dimension_code is dimension]
            if not dimension_findings:
                dimension_grades[dimension] = FindingGrade.INSUFFICIENT_EVIDENCE
                blocking.append(f"missing_dimension_evidence:{dimension.value}")
                continue
            ranks: list[int] = []
            for finding in dimension_findings:
                rank = _GRADE_RANK[finding.grade]
                audit = review.latest_audit(finding.finding_id) if isinstance(findings, EvidenceReview) else None
                if audit is not None and audit.decision in {
                    EvidenceAuditDecision.DOWNGRADED,
                    EvidenceAuditDecision.NEEDS_MORE_EVIDENCE,
                }:
                    rank = max(0, rank - 1)
                if not finding.evidence_ids:
                    rank = 0
                ranks.append(rank)
            # The weakest supported claim controls the dimension.  This is
            # intentionally not an arithmetic average.
            dimension_grades[dimension] = _RANK_GRADE[min(ranks)]

        if blocking:
            recommendation = (
                Recommendation.PAUSE
                if any(
                    reason.startswith(("hard_block", "unresolved_conflict", "finding_rejected")) for reason in blocking
                )
                else Recommendation.VALIDATE_FURTHER
            )
        elif any(grade is FindingGrade.INSUFFICIENT_EVIDENCE for grade in dimension_grades.values()):
            recommendation = Recommendation.VALIDATE_FURTHER
        elif any(grade is FindingGrade.WEAK for grade in dimension_grades.values()):
            recommendation = Recommendation.ADJUST
        elif all(grade in {FindingGrade.STRONG, FindingGrade.MODERATE} for grade in dimension_grades.values()):
            recommendation = Recommendation.PROCEED
        else:
            recommendation = Recommendation.VALIDATE_FURTHER

        return RuleEvaluation(
            standard_version=standard_version,
            dimension_grades=dimension_grades,
            recommendation=recommendation,
            blocking_reasons=tuple(dict.fromkeys(blocking)),
            finding_ids=tuple(finding.finding_id for finding in accepted),
            evidence_ids=tuple(sorted(evidence_ids, key=str)),
        )


def evaluate_rules(
    findings: Iterable[Finding] | EvidenceReview,
    *,
    standard_version: str = "1.0",
) -> RuleEvaluation:
    """Functional convenience wrapper around :class:`RuleEvaluator`."""

    return RuleEvaluator().evaluate(findings, standard_version=standard_version)
