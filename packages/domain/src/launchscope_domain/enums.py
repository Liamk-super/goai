"""Closed vocabularies used by the LaunchScope domain kernel."""

from __future__ import annotations

from enum import StrEnum


class DomainStrEnum(StrEnum):
    """A string enum whose value is stable on the wire."""

    def __str__(self) -> str:
        return self.value


class RunStatus(DomainStrEnum):
    DRAFT = "DRAFT"
    INTAKE = "INTAKE"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    EVIDENCE_REVIEW = "EVIDENCE_REVIEW"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    WAITING_FOR_BUDGET = "WAITING_FOR_BUDGET"
    SYNTHESIZING = "SYNTHESIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"


class StageCode(DomainStrEnum):
    INTAKE = "INTAKE"
    GAP_ANALYSIS = "GAP_ANALYSIS"
    PROFILE_CONFIRMATION = "PROFILE_CONFIRMATION"
    PLANNING = "PLANNING"
    PARALLEL_EVALUATION = "PARALLEL_EVALUATION"
    EVIDENCE_REVIEW = "EVIDENCE_REVIEW"
    REMEDIATION = "REMEDIATION"
    SYNTHESIS = "SYNTHESIS"
    DOSSIER_COMMIT = "DOSSIER_COMMIT"
    VERSION_REGRESSION = "VERSION_REGRESSION"


class StageStatus(DomainStrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    WAITING = "WAITING"


class TaskStatus(DomainStrEnum):
    PENDING = "PENDING"
    BLOCKED = "BLOCKED"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    NEEDS_INPUT = "NEEDS_INPUT"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class DimensionCode(DomainStrEnum):
    PRODUCT_IMPLEMENTATION = "PRODUCT_IMPLEMENTATION"
    USER_USAGE = "USER_USAGE"
    BUSINESS_INVESTMENT = "BUSINESS_INVESTMENT"
    GEO_POLICY_TREND = "GEO_POLICY_TREND"


class FindingGrade(DomainStrEnum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class Recommendation(DomainStrEnum):
    PROCEED = "PROCEED"
    VALIDATE_FURTHER = "VALIDATE_FURTHER"
    ADJUST = "ADJUST"
    PAUSE = "PAUSE"


class EvidenceLevel(DomainStrEnum):
    E0 = "E0"
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"
    E4 = "E4"
    E5 = "E5"


class RiskTier(DomainStrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class NetworkLevel(DomainStrEnum):
    NONE = "NONE"
    PUBLIC_RESEARCH = "PUBLIC_RESEARCH"
    AUTHENTICATED_RESEARCH = "AUTHENTICATED_RESEARCH"
    EXTERNAL_ACTION = "EXTERNAL_ACTION"


class FailureClass(DomainStrEnum):
    TRANSIENT = "TRANSIENT"
    VALIDATION = "VALIDATION"
    AUTHORIZATION = "AUTHORIZATION"
    DEPENDENCY = "DEPENDENCY"
    BUDGET = "BUDGET"
    POLICY = "POLICY"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    BUSINESS = "BUSINESS"


class EvidenceSourceType(DomainStrEnum):
    MATERIAL = "MATERIAL"
    PUBLIC_RESEARCH = "PUBLIC_RESEARCH"
    AUTHENTICATED_RESEARCH = "AUTHENTICATED_RESEARCH"
    DERIVED = "DERIVED"


class EvidenceAuditDecision(DomainStrEnum):
    ACCEPTED = "ACCEPTED"
    DOWNGRADED = "DOWNGRADED"
    REJECTED = "REJECTED"
    NEEDS_MORE_EVIDENCE = "NEEDS_MORE_EVIDENCE"


class ApprovalDecision(DomainStrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ProductVersionStatus(DomainStrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"


class RegressionResult(DomainStrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class EventType(DomainStrEnum):
    PROJECT_CREATED = "project.created"
    PRODUCT_VERSION_SUBMITTED = "product_version.submitted"
    INTAKE_GAP_IDENTIFIED = "intake.gap_identified"
    PROFILE_CONFIRMED = "profile.confirmed"
    EVALUATION_RUN_STARTED = "evaluation.run.started"
    TASK_DISPATCHED = "task.dispatched"
    EVIDENCE_CAPTURED = "evidence.captured"
    FINDING_SUBMITTED = "finding.submitted"
    EVIDENCE_AUDIT_COMPLETED = "evidence.audit_completed"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    RUN_NEEDS_ATTENTION = "run.needs_attention"
    DECISION_SYNTHESIZED = "decision.synthesized"
    DOSSIER_COMMITTED = "dossier.committed"
    VERSION_REGRESSION_COMPLETED = "version.regression_completed"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


STAGE_ORDER: tuple[StageCode, ...] = (
    StageCode.INTAKE,
    StageCode.GAP_ANALYSIS,
    StageCode.PROFILE_CONFIRMATION,
    StageCode.PLANNING,
    StageCode.PARALLEL_EVALUATION,
    StageCode.EVIDENCE_REVIEW,
    StageCode.REMEDIATION,
    StageCode.SYNTHESIS,
    StageCode.DOSSIER_COMMIT,
    StageCode.VERSION_REGRESSION,
)

ALL_DIMENSIONS: tuple[DimensionCode, ...] = (
    DimensionCode.PRODUCT_IMPLEMENTATION,
    DimensionCode.USER_USAGE,
    DimensionCode.BUSINESS_INVESTMENT,
    DimensionCode.GEO_POLICY_TREND,
)
