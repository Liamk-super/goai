"""EvaluationRun aggregate, RunManifest, Stage and Task models."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID, uuid4

from ..enums import STAGE_ORDER, FailureClass, RunStatus, StageCode, StageStatus, TaskStatus
from ..errors import (
    AppendOnlyViolation,
    BudgetError,
    InvariantViolation,
    RetryNotPermittedError,
    TenantScopeViolation,
    ValidationError,
)
from ..services.run_state_machine import RunStateMachine, RunTransitionContext, StageGate
from ..services.task_dag import TaskCompletion, TaskDAG, TaskStateMachine
from ..value_objects import BudgetReservation, TenantScope, _aware, _text, _uuid


def _canonical(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if hasattr(value, "value") and isinstance(value.value, str):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _canonical(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_canonical(item) for item in value]
    return value


def _freeze_value(value: object) -> object:
    """Recursively remove mutable containers from a frozen manifest payload."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class RunManifest:
    """The immutable execution contract frozen before a Run starts."""

    standard_version: str = "1.0"
    manifest_version: str = "1.0"
    material_ids: tuple[UUID, ...] = ()
    agent_versions: Mapping[str, str] = field(default_factory=dict)
    skill_versions: Mapping[str, str] = field(default_factory=dict)
    prompt_versions: Mapping[str, str] = field(default_factory=dict)
    model_versions: Mapping[str, str] = field(default_factory=dict)
    tool_versions: Mapping[str, str] = field(default_factory=dict)
    budget_limits: tuple[BudgetReservation, ...] = ()
    permissions: tuple[str, ...] = ()
    timeout_seconds: int = 900
    security_policy_version: str = "1.0"
    configuration: Mapping[str, object] = field(default_factory=dict)
    frozen: bool = False
    manifest_sha256: str = ""

    def __post_init__(self) -> None:
        self._validate_version(self.standard_version, "standard_version")
        self._validate_version(self.manifest_version, "manifest_version")
        self._validate_version(self.security_policy_version, "security_policy_version")
        object.__setattr__(self, "material_ids", tuple(_uuid(value, "material_id") for value in self.material_ids))
        if len(set(self.material_ids)) != len(self.material_ids):
            raise InvariantViolation("RunManifest material_ids must be unique")
        for field_name in (
            "agent_versions",
            "skill_versions",
            "prompt_versions",
            "model_versions",
            "tool_versions",
            "configuration",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Mapping):
                raise ValidationError(f"{field_name} must be an object", details={"field": field_name})
            object.__setattr__(self, field_name, _freeze_value(value))
        object.__setattr__(self, "budget_limits", tuple(self.budget_limits))
        permissions = tuple(_text(value, "permission", max_length=200) for value in self.permissions)
        object.__setattr__(self, "permissions", permissions)
        if not isinstance(self.timeout_seconds, int) or self.timeout_seconds <= 0:
            raise ValidationError("timeout_seconds must be positive", details={"field": "timeout_seconds"})
        if not isinstance(self.frozen, bool):
            raise ValidationError("frozen must be boolean", details={"field": "frozen"})
        if self.manifest_sha256 and len(self.manifest_sha256) != 64:
            raise ValidationError("manifest_sha256 must be a SHA-256 digest")

    @staticmethod
    def _validate_version(value: str, field_name: str) -> None:
        if not isinstance(value, str) or not value.strip() or value.count(".") != 1:
            raise ValidationError(f"{field_name} must use MAJOR.MINOR form", details={"field": field_name})
        major, minor = value.split(".")
        if not major.isdigit() or not minor.isdigit():
            raise ValidationError(f"{field_name} must use MAJOR.MINOR form", details={"field": field_name})

    def _hash_payload(self) -> dict[str, object]:
        return {
            "standard_version": self.standard_version,
            "manifest_version": self.manifest_version,
            "material_ids": self.material_ids,
            "agent_versions": self.agent_versions,
            "skill_versions": self.skill_versions,
            "prompt_versions": self.prompt_versions,
            "model_versions": self.model_versions,
            "tool_versions": self.tool_versions,
            "budget_limits": self.budget_limits,
            "permissions": self.permissions,
            "timeout_seconds": self.timeout_seconds,
            "security_policy_version": self.security_policy_version,
            "configuration": self.configuration,
        }

    def freeze(self) -> RunManifest:
        if self.frozen:
            return self
        serialized = json.dumps(
            _canonical(self._hash_payload()),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return replace(self, frozen=True, manifest_sha256=digest)

    @property
    def run_manifest_sha256(self) -> str:
        return self.manifest_sha256


@dataclass
class Stage:
    code: StageCode
    status: StageStatus = StageStatus.PENDING
    task_ids: tuple[UUID, ...] = ()
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        self.code = StageCode(self.code)
        self.task_ids = tuple(_uuid(value, "task_id") for value in self.task_ids)

    def start(self, *, at: datetime | None = None) -> Stage:
        if self.status is not StageStatus.PENDING:
            raise InvariantViolation("only a pending stage can start", details={"stage_code": self.code.value})
        self.status = StageStatus.RUNNING
        self.started_at = _aware(at, "at") if at else datetime.now(UTC)
        return self

    def complete(self, *, at: datetime | None = None) -> Stage:
        if self.status is not StageStatus.RUNNING:
            raise InvariantViolation("only a running stage can complete", details={"stage_code": self.code.value})
        self.status = StageStatus.COMPLETED
        self.completed_at = _aware(at, "at") if at else datetime.now(UTC)
        return self


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_schema_corrections: int = 1
    max_transient_retries: int = 0

    def __post_init__(self) -> None:
        if self.max_schema_corrections < 0 or self.max_transient_retries < 0:
            raise ValidationError("retry limits must not be negative")


@dataclass
class Task:
    task_id: UUID
    run_id: UUID
    stage_code: StageCode
    agent_identity_ref: str = "unspecified"
    skill_ref: str = "unspecified"
    skill_version: str = "1.0"
    dependencies: tuple[UUID, ...] = ()
    tool_allowlist: tuple[str, ...] = ()
    budget_slice: BudgetReservation | None = None
    timeout_seconds: int = 300
    success_condition: str = "schema_valid_and_success_condition"
    evidence_requirement: str | None = None
    evidence_required: bool = False
    required: bool = True
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    idempotency_key: str | None = None
    lease_token: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    evidence_ids: tuple[UUID, ...] = ()
    correction_attempts: int = 0
    transient_retries: int = 0
    last_failure_class: FailureClass | None = None
    last_error: str | None = None
    side_effect_started: bool = False

    def __post_init__(self) -> None:
        self.task_id = _uuid(self.task_id, "task_id")
        self.run_id = _uuid(self.run_id, "run_id")
        self.stage_code = StageCode(self.stage_code)
        self.agent_identity_ref = _text(self.agent_identity_ref, "agent_identity_ref", max_length=200)
        self.skill_ref = _text(self.skill_ref, "skill_ref", max_length=200)
        self.skill_version = _text(self.skill_version, "skill_version", max_length=20)
        self.dependencies = tuple(_uuid(value, "dependency") for value in self.dependencies)
        if self.task_id in self.dependencies:
            raise InvariantViolation("task cannot depend on itself", details={"task_id": str(self.task_id)})
        self.tool_allowlist = tuple(_text(value, "tool_id", max_length=200) for value in self.tool_allowlist)
        self.status = TaskStatus(self.status)
        self.evidence_ids = tuple(_uuid(value, "evidence_id") for value in self.evidence_ids)
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise InvariantViolation("task evidence_ids must be unique")
        self.evidence_required = self.evidence_required or bool(self.evidence_requirement)
        if self.timeout_seconds <= 0:
            raise ValidationError("task timeout_seconds must be positive")
        if self.idempotency_key is None:
            self.idempotency_key = f"task:{self.task_id}"
        else:
            self.idempotency_key = _text(self.idempotency_key, "idempotency_key", max_length=200)

    @classmethod
    def create(
        cls,
        run_id: UUID | str,
        stage_code: StageCode,
        *,
        task_id: UUID | str | None = None,
        agent_identity_ref: str = "unspecified",
        skill_ref: str = "unspecified",
        skill_version: str = "1.0",
        dependencies: tuple[UUID, ...] = (),
        required: bool = True,
        evidence_required: bool = False,
        evidence_requirement: str | None = None,
        success_condition: str = "schema_valid_and_success_condition",
        retry_policy: RetryPolicy | None = None,
    ) -> Task:
        return cls(
            task_id=_uuid(task_id or uuid4(), "task_id"),
            run_id=_uuid(run_id, "run_id"),
            stage_code=stage_code,
            agent_identity_ref=agent_identity_ref,
            skill_ref=skill_ref,
            skill_version=skill_version,
            dependencies=dependencies,
            required=required,
            evidence_required=evidence_required,
            evidence_requirement=evidence_requirement,
            success_condition=success_condition,
            retry_policy=retry_policy or RetryPolicy(),
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.NEEDS_ATTENTION,
            TaskStatus.CANCELLED,
        }

    def lease(self, lease_token: str) -> Task:
        TaskStateMachine.transition(self.status, TaskStatus.LEASED)
        self.lease_token = _text(lease_token, "lease_token", max_length=255)
        self.status = TaskStatus.LEASED
        return self

    def start_running(self) -> Task:
        TaskStateMachine.transition(self.status, TaskStatus.RUNNING)
        self.status = TaskStatus.RUNNING
        return self

    def succeed(self, completion: TaskCompletion | None = None) -> Task:
        result = completion or TaskCompletion()
        if not result.schema_valid or not result.success_condition_met:
            raise ValidationError("task cannot succeed before schema and success condition pass")
        if self.evidence_required and not result.evidence_ids:
            raise ValidationError("task cannot succeed without required evidence")
        TaskStateMachine.transition(self.status, TaskStatus.SUCCEEDED)
        self.evidence_ids = tuple(result.evidence_ids)
        self.status = TaskStatus.SUCCEEDED
        return self

    def fail(self, failure_class: FailureClass, reason: str) -> Task:
        failure = FailureClass(failure_class)
        normalized_reason = _text(reason, "reason", max_length=1000)
        if failure in {FailureClass.BUDGET, FailureClass.POLICY, FailureClass.SUBMISSION_UNKNOWN}:
            TaskStateMachine.transition(self.status, TaskStatus.NEEDS_ATTENTION, failure_class=failure)
            self.status = TaskStatus.NEEDS_ATTENTION
        else:
            TaskStateMachine.transition(self.status, TaskStatus.FAILED)
            self.status = TaskStatus.FAILED
        self.last_failure_class = failure
        self.last_error = normalized_reason
        return self

    def request_approval(self) -> Task:
        TaskStateMachine.transition(self.status, TaskStatus.WAITING_FOR_APPROVAL)
        self.status = TaskStatus.WAITING_FOR_APPROVAL
        return self

    def approve(self) -> Task:
        TaskStateMachine.transition(self.status, TaskStatus.RUNNING, approval_valid=True)
        self.status = TaskStatus.RUNNING
        return self

    def expire_lease(self, *, status_known: bool = False) -> Task:
        TaskStateMachine.transition(self.status, TaskStatus.EXPIRED, known_status=status_known)
        self.status = TaskStatus.EXPIRED
        return self

    def retry(self, *, status_known: bool = True, no_side_effect: bool = True) -> Task:
        failure = self.last_failure_class
        if self.status is TaskStatus.EXPIRED:
            available = True
        elif failure is FailureClass.VALIDATION:
            available = self.correction_attempts < self.retry_policy.max_schema_corrections
        elif failure is FailureClass.TRANSIENT:
            available = self.transient_retries < self.retry_policy.max_transient_retries
        else:
            available = False
        if failure in {
            FailureClass.SUBMISSION_UNKNOWN,
            FailureClass.AUTHORIZATION,
            FailureClass.BUDGET,
            FailureClass.POLICY,
        }:
            available = False
        if not available:
            raise RetryNotPermittedError(
                "task failure is not retryable under the fail-closed policy",
                details={"failure_class": failure.value if failure else None},
            )
        TaskStateMachine.transition(
            self.status,
            TaskStatus.PENDING,
            failure_class=failure,
            known_status=status_known,
            no_side_effect=no_side_effect,
            retry_available=True,
        )
        if failure is FailureClass.VALIDATION:
            self.correction_attempts += 1
        if failure is FailureClass.TRANSIENT:
            self.transient_retries += 1
        self.status = TaskStatus.PENDING
        self.lease_token = None
        return self

    def record_evidence(self, evidence_ids: tuple[UUID, ...]) -> Task:
        normalized = tuple(_uuid(value, "evidence_id") for value in evidence_ids)
        if len(set(normalized)) != len(normalized):
            raise InvariantViolation("task evidence references must be unique")
        self.evidence_ids = normalized
        return self


@dataclass(frozen=True, slots=True)
class RunTransitionRecord:
    from_status: RunStatus
    to_status: RunStatus
    reason: str
    occurred_at: datetime
    failure_class: FailureClass | None = None


@dataclass
class EvaluationRun:
    """Evaluation aggregate owning the Run/Stage/Task lifecycle."""

    run_id: UUID
    scope: TenantScope
    product_version_id: UUID
    standard_version: str
    status: RunStatus = RunStatus.DRAFT
    current_stage: StageCode | None = None
    manifest: RunManifest | None = None
    budget_reservations: tuple[BudgetReservation, ...] = ()
    stages: dict[StageCode, Stage] = field(default_factory=dict)
    tasks: dict[UUID, Task] = field(default_factory=dict)
    status_history: list[RunTransitionRecord] = field(default_factory=list)
    gap_identified: bool = False
    profile_confirmed: bool = False
    material_profile_complete: bool = False
    budget_reserved: bool = False
    required_tasks_terminal: bool = False
    audit_ready: bool = False
    approval_valid: bool = False
    decision_committed: bool = False
    report_committed: bool = False
    dossier_committed: bool = False
    last_failure_class: FailureClass | None = None
    attention_reason: str | None = None
    reconciliation_complete: bool = False

    def __post_init__(self) -> None:
        self.run_id = _uuid(self.run_id, "run_id")
        self.product_version_id = _uuid(self.product_version_id, "product_version_id")
        self.standard_version = _text(self.standard_version, "standard_version", max_length=20)
        self.status = RunStatus(self.status)
        if self.scope.project_id is None or self.scope.product_version_id not in {None, self.product_version_id}:
            raise TenantScopeViolation("EvaluationRun is outside its ProductVersion scope")
        if self.scope.run_id not in {None, self.run_id}:
            raise TenantScopeViolation("EvaluationRun scope has another run id")
        self.scope = self.scope.with_product_version(self.product_version_id).with_run(self.run_id)
        if not self.stages:
            self.stages = {code: Stage(code) for code in STAGE_ORDER}
        else:
            self.stages = {StageCode(code): stage for code, stage in self.stages.items()}
        self.tasks = dict(self.tasks)
        self.budget_reservations = tuple(self.budget_reservations)
        self.status_history = list(self.status_history)
        for task in self.tasks.values():
            self._assert_task_scope(task)

    @classmethod
    def create(
        cls,
        scope: TenantScope,
        product_version_id: UUID | str,
        standard_version: str = "1.0",
        *,
        run_id: UUID | str | None = None,
    ) -> EvaluationRun:
        resolved_run_id = _uuid(run_id or scope.run_id or uuid4(), "run_id")
        version_id = _uuid(product_version_id, "product_version_id")
        return cls(
            run_id=resolved_run_id,
            scope=scope,
            product_version_id=version_id,
            standard_version=standard_version,
        )

    @property
    def manifest_frozen(self) -> bool:
        return self.manifest is not None and self.manifest.frozen

    @property
    def retry_blocked(self) -> bool:
        return self.last_failure_class in {
            FailureClass.SUBMISSION_UNKNOWN,
            FailureClass.AUTHORIZATION,
            FailureClass.BUDGET,
            FailureClass.POLICY,
        }

    @property
    def is_terminal(self) -> bool:
        return self.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.EXPIRED}

    def transition_to(
        self,
        target: RunStatus,
        *,
        context: RunTransitionContext | None = None,
        reason: str = "",
        failure_class: FailureClass | None = None,
    ) -> RunStatus:
        ctx = context or self._context(failure_class=failure_class)
        new_status = RunStateMachine.transition(self.status, RunStatus(target), ctx)
        old_status = self.status
        self.status = new_status
        if failure_class is not None:
            self.last_failure_class = FailureClass(failure_class)
        self.status_history.append(
            RunTransitionRecord(
                from_status=old_status,
                to_status=new_status,
                reason=reason,
                occurred_at=datetime.now(UTC),
                failure_class=failure_class,
            )
        )
        return new_status

    def start_intake(self) -> EvaluationRun:
        self.transition_to(RunStatus.INTAKE, reason="authorized intake")
        return self

    def identify_gap(self) -> EvaluationRun:
        self.transition_to(
            RunStatus.WAITING_FOR_USER,
            context=self._context(gap_identified=True),
            reason="gap identified",
        )
        self.gap_identified = True
        return self

    def confirm_profile(self) -> EvaluationRun:
        self.transition_to(RunStatus.PLANNED, context=self._context(profile_confirmed=True), reason="profile confirmed")
        self.profile_confirmed = True
        return self

    def mark_material_profile_complete(self) -> EvaluationRun:
        self.material_profile_complete = True
        if self.status is RunStatus.INTAKE:
            self.transition_to(RunStatus.PLANNED, reason="material and profile complete")
        return self

    def reserve_budget(self, reservations: tuple[BudgetReservation, ...]) -> EvaluationRun:
        if any(reservation.run_id != self.run_id for reservation in reservations):
            raise TenantScopeViolation("budget reservation belongs to another run")
        if any(reservation.reserved > reservation.limit for reservation in reservations):
            raise BudgetError("budget reservation exceeds its limit")
        self.budget_reservations = tuple(reservations)
        self.budget_reserved = True
        if self.status is RunStatus.WAITING_FOR_BUDGET:
            self.transition_to(RunStatus.PLANNED, context=self._context(budget_reserved=True), reason="budget reserved")
        return self

    def budget_unavailable(self) -> EvaluationRun:
        self.transition_to(RunStatus.WAITING_FOR_BUDGET, reason="budget reservation unavailable")
        self.budget_reserved = False
        return self

    def freeze_manifest(self, manifest: RunManifest) -> RunManifest:
        if manifest.standard_version != self.standard_version:
            raise InvariantViolation("manifest standard_version does not match Run")
        frozen = manifest.freeze()
        if self.manifest is not None and self.manifest != frozen:
            raise AppendOnlyViolation("RunManifest is immutable after first freeze")
        self.manifest = frozen
        return frozen

    def start_execution(self) -> EvaluationRun:
        self.transition_to(
            RunStatus.RUNNING,
            context=self._context(manifest_frozen=self.manifest_frozen, budget_reserved=self.budget_reserved),
            reason="manifest frozen and budget reserved",
        )
        return self

    def add_task(self, task: Task) -> Task:
        self._assert_task_scope(task)
        if task.task_id in self.tasks:
            raise AppendOnlyViolation("task_id is already present in this Run")
        self.tasks[task.task_id] = task
        stage = self.stages[task.stage_code]
        stage.task_ids = (*stage.task_ids, task.task_id)
        return task

    def task_dag(self) -> TaskDAG:
        return TaskDAG(self.tasks.values())

    def mark_required_tasks_terminal(self) -> EvaluationRun:
        self.task_dag().validate()
        self.required_tasks_terminal = all(
            not task.required or task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}
            for task in self.tasks.values()
        )
        if not self.required_tasks_terminal:
            raise InvariantViolation("not all required tasks are terminal")
        return self

    def enter_evidence_review(self) -> EvaluationRun:
        self.transition_to(
            RunStatus.EVIDENCE_REVIEW,
            context=self._context(required_tasks_terminal=self.required_tasks_terminal),
            reason="required tasks reached terminal states",
        )
        return self

    def request_approval(self) -> EvaluationRun:
        self.transition_to(RunStatus.WAITING_FOR_APPROVAL, reason="protected action requires approval")
        self.approval_valid = False
        return self

    def resolve_approval(self, approved: bool, *, valid: bool = True, reason: str = "") -> EvaluationRun:
        if approved and valid:
            self.approval_valid = True
            self.transition_to(
                RunStatus.SYNTHESIZING,
                context=self._context(approval_valid=True),
                reason=reason or "approval accepted",
            )
        else:
            self.approval_valid = False
            self.transition_to(
                RunStatus.NEEDS_ATTENTION,
                context=self._context(failure_class=FailureClass.POLICY),
                reason=reason or "approval rejected or expired",
                failure_class=FailureClass.POLICY,
            )
        return self

    def begin_synthesis(self) -> EvaluationRun:
        self.transition_to(
            RunStatus.SYNTHESIZING,
            context=self._context(audit_ready=self.audit_ready),
            reason="evidence review accepted",
        )
        return self

    def mark_audit_ready(self) -> EvaluationRun:
        self.audit_ready = True
        return self

    def mark_decision_committed(self) -> EvaluationRun:
        if self.status is not RunStatus.SYNTHESIZING:
            raise InvariantViolation("decision can only be committed during synthesis")
        self.decision_committed = True
        return self

    def mark_report_committed(self) -> EvaluationRun:
        if self.status is not RunStatus.SYNTHESIZING:
            raise InvariantViolation("report can only be committed during synthesis")
        self.report_committed = True
        return self

    def mark_dossier_committed(self) -> EvaluationRun:
        if self.status is not RunStatus.SYNTHESIZING:
            raise InvariantViolation("dossier can only be committed during synthesis")
        self.dossier_committed = True
        return self

    def complete(self) -> EvaluationRun:
        self.transition_to(
            RunStatus.COMPLETED,
            context=self._context(
                decision_committed=self.decision_committed,
                report_committed=self.report_committed,
                dossier_committed=self.dossier_committed,
            ),
            reason="decision, report and dossier committed",
        )
        return self

    def fail(self, failure_class: FailureClass, reason: str) -> EvaluationRun:
        failure = FailureClass(failure_class)
        if failure is FailureClass.SUBMISSION_UNKNOWN:
            return self.mark_needs_attention(failure, reason)
        self.transition_to(RunStatus.FAILED, reason=_text(reason, "reason", max_length=1000), failure_class=failure)
        return self

    def mark_needs_attention(self, failure_class: FailureClass, reason: str) -> EvaluationRun:
        failure = FailureClass(failure_class)
        normalized = _text(reason, "reason", max_length=1000)
        self.transition_to(
            RunStatus.NEEDS_ATTENTION,
            context=self._context(failure_class=failure),
            reason=normalized,
            failure_class=failure,
        )
        self.attention_reason = normalized
        return self

    def resume(self, *, reconciliation_complete: bool = False) -> EvaluationRun:
        if self.last_failure_class is FailureClass.SUBMISSION_UNKNOWN:
            self.reconciliation_complete = reconciliation_complete
        self.transition_to(
            RunStatus.RUNNING,
            context=self._context(
                human_resume=True,
                reconciliation_complete=self.reconciliation_complete,
                failure_class=self.last_failure_class,
                manifest_frozen=self.manifest_frozen,
                budget_reserved=self.budget_reserved,
            ),
            reason="explicit human resume",
        )
        return self

    def cancel(self) -> EvaluationRun:
        self.transition_to(RunStatus.CANCELLED, reason="controlled user stop")
        return self

    def expire(self) -> EvaluationRun:
        self.transition_to(
            RunStatus.EXPIRED,
            context=self._context(response_deadline_passed=True),
            reason="response deadline passed",
        )
        return self

    def enter_stage(self, code: StageCode) -> Stage:
        stage_code = StageCode(code)
        StageGate.assert_entry(self.current_stage, stage_code)
        stage = self.stages[stage_code]
        stage.start()
        self.current_stage = stage_code
        return stage

    def complete_stage(self, code: StageCode) -> Stage:
        stage = self.stages[StageCode(code)]
        stage.complete()
        return stage

    def _context(self, **overrides: object) -> RunTransitionContext:
        values: dict[str, object] = {
            "gap_identified": self.gap_identified,
            "profile_confirmed": self.profile_confirmed,
            "material_profile_complete": self.material_profile_complete,
            "manifest_frozen": self.manifest_frozen,
            "budget_reserved": self.budget_reserved,
            "required_tasks_terminal": self.required_tasks_terminal,
            "audit_ready": self.audit_ready,
            "approval_valid": self.approval_valid,
            "decision_committed": self.decision_committed,
            "report_committed": self.report_committed,
            "dossier_committed": self.dossier_committed,
            "failure_class": self.last_failure_class,
            "reconciliation_complete": self.reconciliation_complete,
        }
        values.update(overrides)
        return RunTransitionContext(**values)  # type: ignore[arg-type]

    def _assert_task_scope(self, task: Task) -> None:
        if task.run_id != self.run_id:
            raise TenantScopeViolation("Task belongs to another Run")
        if task.stage_code not in self.stages:
            raise InvariantViolation("Task stage is not part of the fixed stage set")
