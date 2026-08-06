"""DecisionReport aggregate for rule-backed decisions and explanations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import UUID, uuid4

from ..enums import ALL_DIMENSIONS, DimensionCode, FindingGrade, Recommendation
from ..errors import (
    AppendOnlyViolation,
    InvariantViolation,
    TenantScopeViolation,
    ValidationError,
)
from ..services.rule_evaluator import RuleEvaluation
from ..value_objects import TenantScope, _aware, _text, _uuid


@dataclass(frozen=True, slots=True)
class Decision:
    decision_id: UUID
    scope: TenantScope
    run_id: UUID
    standard_version: str
    recommendation: Recommendation
    dimension_grades: Mapping[DimensionCode, FindingGrade]
    finding_ids: tuple[UUID, ...]
    blocking_reasons: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    supersedes_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _uuid(self.decision_id, "decision_id"))
        object.__setattr__(self, "run_id", _uuid(self.run_id, "run_id"))
        if self.scope.run_id not in {None, self.run_id}:
            raise TenantScopeViolation("decision is outside its Run scope")
        object.__setattr__(self, "recommendation", Recommendation(self.recommendation))
        object.__setattr__(self, "standard_version", _text(self.standard_version, "standard_version", max_length=20))
        normalized = {DimensionCode(key): FindingGrade(value) for key, value in self.dimension_grades.items()}
        missing = [dimension.value for dimension in ALL_DIMENSIONS if dimension not in normalized]
        if missing:
            raise ValidationError("decision must contain all four dimensions", details={"missing_dimensions": missing})
        object.__setattr__(self, "dimension_grades", MappingProxyType(normalized))
        finding_ids = tuple(_uuid(value, "finding_id") for value in self.finding_ids)
        if len(set(finding_ids)) != len(finding_ids):
            raise InvariantViolation("decision finding_ids must be unique")
        object.__setattr__(self, "finding_ids", finding_ids)
        blocking_reasons = tuple(
            _text(value, "blocking_reason", max_length=1000) for value in self.blocking_reasons
        )
        object.__setattr__(self, "blocking_reasons", blocking_reasons)
        object.__setattr__(self, "created_at", _aware(self.created_at, "created_at"))
        if self.supersedes_id is not None:
            object.__setattr__(self, "supersedes_id", _uuid(self.supersedes_id, "supersedes_id"))


@dataclass(frozen=True, slots=True)
class Report:
    report_id: UUID
    scope: TenantScope
    run_id: UUID
    decision_id: UUID
    explanation: str
    action_items: tuple[str, ...] = ()
    finding_ids: tuple[UUID, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    supersedes_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "report_id", _uuid(self.report_id, "report_id"))
        object.__setattr__(self, "run_id", _uuid(self.run_id, "run_id"))
        object.__setattr__(self, "decision_id", _uuid(self.decision_id, "decision_id"))
        if self.scope.run_id not in {None, self.run_id}:
            raise TenantScopeViolation("report is outside its Run scope")
        object.__setattr__(self, "explanation", _text(self.explanation, "explanation", max_length=20000))
        actions = tuple(_text(value, "action_item", max_length=1000) for value in self.action_items)
        if len(actions) > 3:
            raise ValidationError("a Report may contain at most three action items")
        object.__setattr__(self, "action_items", actions)
        finding_ids = tuple(_uuid(value, "finding_id") for value in self.finding_ids)
        evidence_ids = tuple(_uuid(value, "evidence_id") for value in self.evidence_ids)
        if len(set(finding_ids)) != len(finding_ids) or len(set(evidence_ids)) != len(evidence_ids):
            raise InvariantViolation("report references must be unique")
        object.__setattr__(self, "finding_ids", finding_ids)
        object.__setattr__(self, "evidence_ids", evidence_ids)
        object.__setattr__(self, "created_at", _aware(self.created_at, "created_at"))
        if self.supersedes_id is not None:
            object.__setattr__(self, "supersedes_id", _uuid(self.supersedes_id, "supersedes_id"))


@dataclass
class DecisionReport:
    """Append-only Decision and Report history for a Run."""

    scope: TenantScope
    standard_version: str
    decisions: list[Decision] = field(default_factory=list)
    reports: list[Report] = field(default_factory=list)
    committed_report_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.scope.run_id is None:
            raise ValidationError("DecisionReport requires run scope")
        self.standard_version = _text(self.standard_version, "standard_version", max_length=20)
        self.decisions = list(self.decisions)
        self.reports = list(self.reports)
        if any(decision.run_id != self.scope.run_id for decision in self.decisions):
            raise TenantScopeViolation("decision belongs to another Run")
        if any(report.run_id != self.scope.run_id for report in self.reports):
            raise TenantScopeViolation("report belongs to another Run")
        if len({decision.decision_id for decision in self.decisions}) != len(self.decisions):
            raise AppendOnlyViolation("decision_id is duplicated in Decision history")
        if len({report.report_id for report in self.reports}) != len(self.reports):
            raise AppendOnlyViolation("report_id is duplicated in Report history")

    @property
    def decision_history(self) -> tuple[Decision, ...]:
        return tuple(self.decisions)

    @property
    def report_history(self) -> tuple[Report, ...]:
        return tuple(self.reports)

    @property
    def current_decision(self) -> Decision | None:
        return self.decisions[-1] if self.decisions else None

    @property
    def current_report(self) -> Report | None:
        return self.reports[-1] if self.reports else None

    def synthesize(
        self,
        evaluation: RuleEvaluation,
        *,
        explanation: str,
        action_items: tuple[str, ...] = (),
        decision_id: UUID | str | None = None,
        report_id: UUID | str | None = None,
        supersedes_decision_id: UUID | str | None = None,
        supersedes_report_id: UUID | str | None = None,
    ) -> tuple[Decision, Report]:
        if evaluation.standard_version != self.standard_version:
            raise InvariantViolation("rule evaluation standard_version does not match DecisionReport")
        if self.decisions and supersedes_decision_id is None:
            raise AppendOnlyViolation("a new Decision must explicitly supersede the current Decision")
        if self.reports and supersedes_report_id is None:
            raise AppendOnlyViolation("a new Report must explicitly supersede the current Report")
        if (
            self.decisions
            and _uuid(supersedes_decision_id or uuid4(), "supersedes_decision_id")
            != self.decisions[-1].decision_id
        ):
            raise AppendOnlyViolation("a new Decision must supersede the current Decision")
        if (
            self.reports
            and _uuid(supersedes_report_id or uuid4(), "supersedes_report_id")
            != self.reports[-1].report_id
        ):
            raise AppendOnlyViolation("a new Report must supersede the current Report")
        decision = Decision(
            decision_id=_uuid(decision_id or uuid4(), "decision_id"),
            scope=self.scope,
            run_id=self.scope.run_id or uuid4(),
            standard_version=self.standard_version,
            recommendation=evaluation.recommendation,
            dimension_grades=evaluation.dimension_grades,
            finding_ids=evaluation.finding_ids,
            blocking_reasons=evaluation.blocking_reasons,
            supersedes_id=(
                _uuid(supersedes_decision_id, "supersedes_decision_id")
                if supersedes_decision_id
                else None
            ),
        )
        report = Report(
            report_id=_uuid(report_id or uuid4(), "report_id"),
            scope=self.scope,
            run_id=self.scope.run_id or uuid4(),
            decision_id=decision.decision_id,
            explanation=explanation,
            action_items=action_items,
            finding_ids=evaluation.finding_ids,
            evidence_ids=evaluation.evidence_ids,
            supersedes_id=(
                _uuid(supersedes_report_id, "supersedes_report_id") if supersedes_report_id else None
            ),
        )
        self.decisions.append(decision)
        self.reports.append(report)
        return decision, report

    def commit_report(self, report_id: UUID | str) -> Report:
        resolved_id = _uuid(report_id, "report_id")
        if self.committed_report_id is not None:
            raise AppendOnlyViolation("a DecisionReport commit cannot be overwritten")
        for report in self.reports:
            if report.report_id == resolved_id:
                self.committed_report_id = resolved_id
                return report
        raise ValidationError("report was not found")
