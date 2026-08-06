"""EvidenceReview aggregate with tenant-safe, append-only findings."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ..enums import DimensionCode, EvidenceAuditDecision, EvidenceLevel, FindingGrade
from ..errors import (
    AppendOnlyViolation,
    InvariantViolation,
    MissingEvidenceError,
    TenantScopeViolation,
    ValidationError,
)
from ..value_objects import EvidenceRef, TenantScope, TimeScope, _aware, _text, _uuid


def _scope_matches(expected: TenantScope, actual: TenantScope) -> bool:
    return (
        expected.tenant_id == actual.tenant_id
        and expected.workspace_id == actual.workspace_id
        and expected.project_id == actual.project_id
        and expected.product_version_id == actual.product_version_id
        and expected.run_id == actual.run_id
    )


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: UUID
    scope: TenantScope
    ref: EvidenceRef
    time_scope: TimeScope = field(default_factory=TimeScope)
    captured_by_task_id: UUID | None = None
    summary: str = ""
    simulated: bool = False
    supersedes_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _uuid(self.evidence_id, "evidence_id"))
        if self.ref.evidence_id != self.evidence_id:
            raise InvariantViolation("EvidenceRef.evidence_id must match Evidence.evidence_id")
        if self.scope.run_id is None or self.scope.project_id is None or self.scope.product_version_id is None:
            raise ValidationError("Evidence requires project, ProductVersion and Run scope")
        if self.captured_by_task_id is not None:
            object.__setattr__(self, "captured_by_task_id", _uuid(self.captured_by_task_id, "captured_by_task_id"))
        if self.summary:
            object.__setattr__(self, "summary", _text(self.summary, "summary", max_length=4000))
        if not isinstance(self.simulated, bool):
            raise ValidationError("simulated must be boolean")
        if self.supersedes_id is not None:
            object.__setattr__(self, "supersedes_id", _uuid(self.supersedes_id, "supersedes_id"))

    @classmethod
    def create(
        cls,
        scope: TenantScope,
        *,
        evidence_id: UUID | str | None = None,
        object_key: str,
        sha256: str,
        mime_type: str,
        source_type: str,
        trust_level: EvidenceLevel | str,
        size_bytes: int = 0,
        time_scope: TimeScope | None = None,
        captured_by_task_id: UUID | str | None = None,
        summary: str = "",
        simulated: bool = False,
        supersedes_id: UUID | str | None = None,
    ) -> Evidence:
        resolved_id = _uuid(evidence_id or uuid4(), "evidence_id")
        return cls(
            evidence_id=resolved_id,
            scope=scope,
            ref=EvidenceRef(resolved_id, object_key, sha256, mime_type, source_type, str(trust_level), size_bytes),
            time_scope=time_scope or TimeScope(),
            captured_by_task_id=_uuid(captured_by_task_id, "captured_by_task_id") if captured_by_task_id else None,
            summary=summary,
            simulated=simulated,
            supersedes_id=_uuid(supersedes_id, "supersedes_id") if supersedes_id else None,
        )


@dataclass(frozen=True, slots=True)
class Finding:
    finding_id: UUID
    scope: TenantScope
    dimension_code: DimensionCode
    grade: FindingGrade
    statement: str
    evidence_ids: tuple[UUID, ...] = ()
    is_hypothesis: bool = False
    submitted_by: str = "unknown"
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    supersedes_id: UUID | None = None
    simulated: bool = False
    hard_block: bool = False
    block_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "finding_id", _uuid(self.finding_id, "finding_id"))
        object.__setattr__(self, "dimension_code", DimensionCode(self.dimension_code))
        object.__setattr__(self, "grade", FindingGrade(self.grade))
        object.__setattr__(self, "statement", _text(self.statement, "statement", max_length=10000))
        normalized_evidence = tuple(_uuid(value, "evidence_id") for value in self.evidence_ids)
        if len(set(normalized_evidence)) != len(normalized_evidence):
            raise InvariantViolation("finding evidence_ids must be unique")
        object.__setattr__(self, "evidence_ids", normalized_evidence)
        object.__setattr__(self, "submitted_by", _text(self.submitted_by, "submitted_by", max_length=255))
        object.__setattr__(self, "submitted_at", _aware(self.submitted_at, "submitted_at"))
        if self.supersedes_id is not None:
            object.__setattr__(self, "supersedes_id", _uuid(self.supersedes_id, "supersedes_id"))
        if self.block_reason is not None:
            object.__setattr__(self, "block_reason", _text(self.block_reason, "block_reason", max_length=1000))

    @classmethod
    def create(
        cls,
        scope: TenantScope,
        dimension_code: DimensionCode,
        grade: FindingGrade,
        statement: str,
        *,
        finding_id: UUID | str | None = None,
        evidence_ids: tuple[UUID, ...] = (),
        is_hypothesis: bool = False,
        submitted_by: str = "unknown",
        supersedes_id: UUID | str | None = None,
        simulated: bool = False,
        hard_block: bool = False,
        block_reason: str | None = None,
    ) -> Finding:
        return cls(
            finding_id=_uuid(finding_id or uuid4(), "finding_id"),
            scope=scope,
            dimension_code=dimension_code,
            grade=grade,
            statement=statement,
            evidence_ids=evidence_ids,
            is_hypothesis=is_hypothesis,
            submitted_by=submitted_by,
            supersedes_id=_uuid(supersedes_id, "supersedes_id") if supersedes_id else None,
            simulated=simulated,
            hard_block=hard_block,
            block_reason=block_reason,
        )

    @classmethod
    def hypothesis(
        cls,
        scope: TenantScope,
        dimension_code: DimensionCode,
        statement: str,
        *,
        finding_id: UUID | str | None = None,
        submitted_by: str = "unknown",
    ) -> Finding:
        return cls.create(
            scope,
            dimension_code,
            FindingGrade.INSUFFICIENT_EVIDENCE,
            statement,
            finding_id=finding_id,
            is_hypothesis=True,
            submitted_by=submitted_by,
        )


@dataclass(frozen=True, slots=True)
class ConflictRecord:
    conflict_id: UUID
    scope: TenantScope
    finding_ids: tuple[UUID, ...]
    reason: str
    resolved: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "conflict_id", _uuid(self.conflict_id, "conflict_id"))
        ids = tuple(_uuid(value, "finding_id") for value in self.finding_ids)
        if len(ids) < 2 or len(set(ids)) != len(ids):
            raise ValidationError("a conflict must reference at least two distinct findings")
        object.__setattr__(self, "finding_ids", ids)
        object.__setattr__(self, "reason", _text(self.reason, "reason", max_length=2000))


@dataclass(frozen=True, slots=True)
class EvidenceAudit:
    audit_id: UUID
    finding_id: UUID
    decision: EvidenceAuditDecision
    auditor_id: str
    reason: str = ""
    audited_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "audit_id", _uuid(self.audit_id, "audit_id"))
        object.__setattr__(self, "finding_id", _uuid(self.finding_id, "finding_id"))
        object.__setattr__(self, "decision", EvidenceAuditDecision(self.decision))
        object.__setattr__(self, "auditor_id", _text(self.auditor_id, "auditor_id", max_length=255))
        if self.reason:
            object.__setattr__(self, "reason", _text(self.reason, "reason", max_length=2000))
        object.__setattr__(self, "audited_at", _aware(self.audited_at, "audited_at"))


@dataclass
class EvidenceReview:
    """Evidence and findings are append-only facts inside a Run scope."""

    scope: TenantScope
    evidence: dict[UUID, Evidence] = field(default_factory=dict)
    findings: dict[UUID, Finding] = field(default_factory=dict)
    conflicts: dict[UUID, ConflictRecord] = field(default_factory=dict)
    audits: list[EvidenceAudit] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.scope.run_id is None or self.scope.project_id is None or self.scope.product_version_id is None:
            raise ValidationError("EvidenceReview requires project, ProductVersion and Run scope")
        self.evidence = dict(self.evidence)
        self.findings = dict(self.findings)
        self.conflicts = dict(self.conflicts)
        self.audits = list(self.audits)
        for item in self.evidence.values():
            self._assert_scope(item.scope)
        for finding in self.findings.values():
            self._assert_scope(finding.scope)
            self._validate_finding_references(finding)

    @property
    def evidence_items(self) -> tuple[Evidence, ...]:
        return tuple(self.evidence.values())

    @property
    def finding_items(self) -> tuple[Finding, ...]:
        return tuple(self.findings.values())

    @property
    def audit_items(self) -> tuple[EvidenceAudit, ...]:
        return tuple(self.audits)

    @property
    def unresolved_conflicts(self) -> tuple[ConflictRecord, ...]:
        return tuple(conflict for conflict in self.conflicts.values() if not conflict.resolved)

    def add_evidence(self, evidence: Evidence) -> Evidence:
        self._assert_scope(evidence.scope)
        if evidence.evidence_id in self.evidence:
            raise AppendOnlyViolation("evidence_id is already present")
        if evidence.supersedes_id is not None and evidence.supersedes_id not in self.evidence:
            raise InvariantViolation("superseded evidence must already exist")
        self.evidence[evidence.evidence_id] = evidence
        return evidence

    def submit_finding(self, finding: Finding) -> Finding:
        self._assert_scope(finding.scope)
        if finding.finding_id in self.findings:
            raise AppendOnlyViolation("finding_id is already present")
        if not finding.evidence_ids and not finding.is_hypothesis:
            finding = replace(
                finding,
                grade=FindingGrade.INSUFFICIENT_EVIDENCE,
                is_hypothesis=True,
                hard_block=False,
                block_reason=None,
            )
        self._validate_finding_references(finding)
        if finding.supersedes_id is not None and finding.supersedes_id not in self.findings:
            raise InvariantViolation("superseded finding must already exist")
        self.findings[finding.finding_id] = finding
        return finding

    def add_conflict(self, conflict: ConflictRecord) -> ConflictRecord:
        self._assert_scope(conflict.scope)
        if conflict.conflict_id in self.conflicts:
            raise AppendOnlyViolation("conflict_id is already present")
        if any(finding_id not in self.findings for finding_id in conflict.finding_ids):
            raise InvariantViolation("conflict references an unknown finding")
        self.conflicts[conflict.conflict_id] = conflict
        return conflict

    def audit_finding(
        self,
        finding_id: UUID | str,
        decision: EvidenceAuditDecision,
        *,
        auditor_id: str,
        reason: str = "",
        audit_id: UUID | str | None = None,
    ) -> EvidenceAudit:
        resolved_id = _uuid(finding_id, "finding_id")
        try:
            finding = self.findings[resolved_id]
        except KeyError as exc:
            raise ValidationError("finding was not found") from exc
        normalized_auditor = _text(auditor_id, "auditor_id", max_length=255)
        if normalized_auditor == finding.submitted_by:
            raise InvariantViolation("the finding author cannot audit their own finding")
        audit = EvidenceAudit(
            audit_id=_uuid(audit_id or uuid4(), "audit_id"),
            finding_id=resolved_id,
            decision=decision,
            auditor_id=normalized_auditor,
            reason=reason,
        )
        self.audits.append(audit)
        return audit

    def latest_audit(self, finding_id: UUID | str) -> EvidenceAudit | None:
        resolved_id = _uuid(finding_id, "finding_id")
        for audit in reversed(self.audits):
            if audit.finding_id == resolved_id:
                return audit
        return None

    def replace_finding(self, finding_id: UUID | str, replacement: Finding) -> None:
        raise AppendOnlyViolation(
            "original findings cannot be overwritten; submit a superseding finding",
            details={"finding_id": str(_uuid(finding_id, "finding_id")), "replacement_id": str(replacement.finding_id)},
        )

    def _assert_scope(self, resource_scope: TenantScope) -> None:
        if not _scope_matches(self.scope, resource_scope):
            raise TenantScopeViolation(
                "EvidenceReview resource is outside the Run tenant scope",
                details={
                    "expected_tenant_id": str(self.scope.tenant_id),
                    "actual_tenant_id": str(resource_scope.tenant_id),
                },
            )

    def _validate_finding_references(self, finding: Finding) -> None:
        missing = tuple(evidence_id for evidence_id in finding.evidence_ids if evidence_id not in self.evidence)
        if missing:
            raise MissingEvidenceError(
                "finding references evidence outside this review",
                details={"missing_evidence_ids": [str(value) for value in missing]},
            )
