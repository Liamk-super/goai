"""Pure Run and Stage state machines with fail-closed guards."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..enums import STAGE_ORDER, FailureClass, RunStatus, StageCode, StageStatus
from ..errors import InvalidTransitionError


@dataclass(frozen=True, slots=True)
class RunTransitionContext:
    """Facts an application service must prove before a Run transition."""

    gap_identified: bool = False
    profile_confirmed: bool = False
    material_profile_complete: bool = False
    manifest_frozen: bool = False
    budget_reserved: bool = False
    required_tasks_terminal: bool = False
    audit_ready: bool = False
    approval_valid: bool = False
    decision_committed: bool = False
    report_committed: bool = False
    dossier_committed: bool = False
    response_deadline_passed: bool = False
    known_terminal_failure: bool = False
    failure_class: FailureClass | None = None
    human_resume: bool = False
    reconciliation_complete: bool = False


@dataclass(frozen=True, slots=True)
class TransitionCheck:
    """Structured result for callers that do not want exceptions."""

    allowed: bool
    current: RunStatus
    target: RunStatus
    reason: str = ""
    code: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "current": self.current.value,
            "target": self.target.value,
            "reason": self.reason,
            "code": self.code,
        }


_RUN_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = {
    RunStatus.DRAFT: frozenset({RunStatus.INTAKE, RunStatus.CANCELLED}),
    RunStatus.INTAKE: frozenset({RunStatus.WAITING_FOR_USER, RunStatus.PLANNED, RunStatus.FAILED}),
    RunStatus.WAITING_FOR_USER: frozenset({RunStatus.PLANNED, RunStatus.EXPIRED, RunStatus.CANCELLED}),
    RunStatus.PLANNED: frozenset({RunStatus.WAITING_FOR_BUDGET, RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.EVIDENCE_REVIEW,
            RunStatus.NEEDS_ATTENTION,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.EVIDENCE_REVIEW: frozenset(
        {RunStatus.WAITING_FOR_APPROVAL, RunStatus.SYNTHESIZING, RunStatus.NEEDS_ATTENTION}
    ),
    RunStatus.WAITING_FOR_APPROVAL: frozenset({RunStatus.SYNTHESIZING, RunStatus.NEEDS_ATTENTION}),
    RunStatus.WAITING_FOR_BUDGET: frozenset({RunStatus.PLANNED}),
    RunStatus.SYNTHESIZING: frozenset({RunStatus.COMPLETED, RunStatus.FAILED}),
    RunStatus.NEEDS_ATTENTION: frozenset({RunStatus.RUNNING}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.EXPIRED: frozenset(),
}


class RunStateMachine:
    """The only legal Run state transitions for V0.1."""

    @classmethod
    def check(
        cls,
        current: RunStatus,
        target: RunStatus,
        context: RunTransitionContext | None = None,
    ) -> TransitionCheck:
        ctx = context or RunTransitionContext()
        try:
            current_status = RunStatus(current)
            target_status = RunStatus(target)
        except ValueError:
            return TransitionCheck(False, current, target, "unknown status", "UNKNOWN_STATUS")

        if target_status not in _RUN_TRANSITIONS.get(current_status, frozenset()):
            return TransitionCheck(
                False,
                current_status,
                target_status,
                "transition is not in the V0.1 transition table",
                "ILLEGAL_TRANSITION",
            )

        reason = cls._guard_reason(current_status, target_status, ctx)
        if reason:
            return TransitionCheck(False, current_status, target_status, reason, "GUARD_FAILED")
        return TransitionCheck(True, current_status, target_status)

    @classmethod
    def transition(
        cls,
        current: RunStatus,
        target: RunStatus,
        context: RunTransitionContext | None = None,
    ) -> RunStatus:
        check = cls.check(current, target, context)
        if not check.allowed:
            raise InvalidTransitionError(
                check.current,
                check.target,
                reason=check.reason,
                details={"code": check.code},
            )
        return check.target

    @staticmethod
    def _guard_reason(current: RunStatus, target: RunStatus, ctx: RunTransitionContext) -> str:
        if current is RunStatus.INTAKE and target is RunStatus.WAITING_FOR_USER and not ctx.gap_identified:
            return "gap diagnosis must exist before waiting for the user"
        if (
            current is RunStatus.INTAKE
            and target is RunStatus.PLANNED
            and not (ctx.profile_confirmed or ctx.material_profile_complete)
        ):
            return "a complete material/profile snapshot is required"
        if current is RunStatus.WAITING_FOR_USER and target is RunStatus.PLANNED and not ctx.profile_confirmed:
            return "the user must confirm the ProductProfile"
        if current is RunStatus.WAITING_FOR_USER and target is RunStatus.EXPIRED and not ctx.response_deadline_passed:
            return "the response deadline has not passed"
        if current is RunStatus.PLANNED and target is RunStatus.WAITING_FOR_BUDGET and ctx.budget_reserved:
            return "a run with a reservation cannot enter WAITING_FOR_BUDGET"
        if current is RunStatus.PLANNED and target is RunStatus.RUNNING:
            if not ctx.manifest_frozen:
                return "RunManifest must be frozen before execution"
            if not ctx.budget_reserved:
                return "budget must be reserved before execution"
        if current is RunStatus.RUNNING and target is RunStatus.EVIDENCE_REVIEW and not ctx.required_tasks_terminal:
            return "all required tasks must be terminal before evidence review"
        if current is RunStatus.RUNNING and target is RunStatus.NEEDS_ATTENTION and ctx.failure_class is None:
            return "fail-closed transition requires a failure class"
        if (
            current is RunStatus.EVIDENCE_REVIEW
            and target is RunStatus.NEEDS_ATTENTION
            and ctx.failure_class is None
        ):
            return "fail-closed transition requires a failure class"
        if current is RunStatus.EVIDENCE_REVIEW and target is RunStatus.SYNTHESIZING and not ctx.audit_ready:
            return "evidence audit and conflict checks must be complete"
        if current is RunStatus.EVIDENCE_REVIEW and target is RunStatus.WAITING_FOR_APPROVAL and ctx.approval_valid:
            return "an already valid approval does not require waiting"
        if current is RunStatus.WAITING_FOR_APPROVAL and target is RunStatus.SYNTHESIZING and not ctx.approval_valid:
            return "a valid one-time approval is required"
        if (
            current is RunStatus.WAITING_FOR_APPROVAL
            and target is RunStatus.NEEDS_ATTENTION
            and ctx.failure_class not in {FailureClass.AUTHORIZATION, FailureClass.POLICY, FailureClass.BUSINESS}
        ):
            return "approval rejection or expiry must be classified as a policy failure"
        if (
            current is RunStatus.SYNTHESIZING
            and target is RunStatus.COMPLETED
            and not (ctx.decision_committed and ctx.report_committed and ctx.dossier_committed)
        ):
            return "decision, report and dossier commit must all be durable"
        if current is RunStatus.NEEDS_ATTENTION and target is RunStatus.RUNNING and not ctx.human_resume:
            return "NEEDS_ATTENTION requires an explicit human resume"
        if (
            current is RunStatus.NEEDS_ATTENTION
            and target is RunStatus.RUNNING
            and ctx.failure_class is FailureClass.SUBMISSION_UNKNOWN
            and not ctx.reconciliation_complete
        ):
            return "SUBMISSION_UNKNOWN requires versioned reconciliation before resume"
        return ""


class StageGate:
    """Enforces the fixed ten-stage order independently of RunStatus."""

    @staticmethod
    def next_stage(current: StageCode | None) -> StageCode:
        if current is None:
            return STAGE_ORDER[0]
        try:
            index = STAGE_ORDER.index(StageCode(current))
        except ValueError as exc:
            raise InvalidTransitionError(current, "next", reason="unknown stage") from exc
        if index == len(STAGE_ORDER) - 1:
            raise InvalidTransitionError(current, "next", reason="all stages are already complete")
        return STAGE_ORDER[index + 1]

    @staticmethod
    def check_entry(current: StageCode | None, requested: StageCode) -> TransitionCheck:
        requested_code = StageCode(requested)
        expected = StageGate.next_stage(current)
        if requested_code is not expected:
            current_status = RunStatus.DRAFT if current is None else RunStatus.RUNNING
            return TransitionCheck(
                False,
                current_status,
                current_status,
                f"stage gate requires {expected.value}, received {requested_code.value}",
                "STAGE_ORDER_VIOLATION",
            )
        return TransitionCheck(True, RunStatus.RUNNING, RunStatus.RUNNING)

    @staticmethod
    def assert_entry(current: StageCode | None, requested: StageCode) -> StageCode:
        check = StageGate.check_entry(current, requested)
        if not check.allowed:
            raise InvalidTransitionError(
                current or "NONE",
                requested,
                reason=check.reason,
                details={"code": check.code},
            )
        return StageCode(requested)


def stage_status_is_terminal(status: StageStatus) -> bool:
    """Return whether a Stage has reached a durable terminal outcome."""

    return status in {StageStatus.COMPLETED, StageStatus.FAILED}


# Compatibility names for application code that spells out the aggregate.
RunStatusMachine = RunStateMachine
TransitionContext = RunTransitionContext
