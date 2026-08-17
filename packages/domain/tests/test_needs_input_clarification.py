"""ADR 0004: the Agent-initiated NEEDS_INPUT clarification loop."""

from __future__ import annotations

from launchscope_domain import (
    FailureClass,
    RunStateMachine,
    RunStatus,
    TaskStatus,
)
from launchscope_domain.services.run_state_machine import RunTransitionContext
from launchscope_domain.services.task_dag import TaskStateMachine


def test_task_enters_needs_input_only_with_an_unanswered_request() -> None:
    refused = TaskStateMachine.check(TaskStatus.RUNNING, TaskStatus.NEEDS_INPUT)
    assert not refused.allowed
    assert refused.code == "INFORMATION_REQUEST_REQUIRED"

    allowed = TaskStateMachine.check(
        TaskStatus.RUNNING, TaskStatus.NEEDS_INPUT, unanswered_information_request=True
    )
    assert allowed.allowed


def test_task_leaves_needs_input_only_when_every_request_is_answered() -> None:
    refused = TaskStateMachine.check(TaskStatus.NEEDS_INPUT, TaskStatus.PENDING)
    assert not refused.allowed
    assert refused.code == "INFORMATION_REQUEST_UNANSWERED"

    allowed = TaskStateMachine.check(
        TaskStatus.NEEDS_INPUT, TaskStatus.PENDING, information_requests_answered=True
    )
    assert allowed.allowed


def test_needs_input_is_not_the_fail_closed_attention_state() -> None:
    # NEEDS_ATTENTION stays terminal and operator-owned.
    assert TaskStateMachine._transitions[TaskStatus.NEEDS_ATTENTION] == frozenset()
    # A clarification never becomes an attention state by itself.
    assert TaskStatus.NEEDS_ATTENTION not in TaskStateMachine._transitions[TaskStatus.NEEDS_INPUT]
    assert TaskStateMachine._transitions[TaskStatus.NEEDS_INPUT] == frozenset(
        {TaskStatus.PENDING, TaskStatus.CANCELLED}
    )


def test_run_pauses_for_clarification_only_with_an_unanswered_request() -> None:
    refused = RunStateMachine.check(RunStatus.RUNNING, RunStatus.WAITING_FOR_USER)
    assert not refused.allowed
    assert refused.code == "GUARD_FAILED"

    allowed = RunStateMachine.check(
        RunStatus.RUNNING,
        RunStatus.WAITING_FOR_USER,
        RunTransitionContext(unanswered_information_request=True),
    )
    assert allowed.allowed


def test_clarification_carries_no_failure_class() -> None:
    check = RunStateMachine.check(
        RunStatus.RUNNING,
        RunStatus.WAITING_FOR_USER,
        RunTransitionContext(
            unanswered_information_request=True, failure_class=FailureClass.POLICY
        ),
    )
    assert not check.allowed
    assert "not a failure" in check.reason


def test_run_resumes_only_after_answers_and_manager_impact_assessment() -> None:
    unanswered = RunStateMachine.check(
        RunStatus.WAITING_FOR_USER, RunStatus.RUNNING, RunTransitionContext()
    )
    assert not unanswered.allowed

    unassessed = RunStateMachine.check(
        RunStatus.WAITING_FOR_USER,
        RunStatus.RUNNING,
        RunTransitionContext(information_requests_answered=True),
    )
    assert not unassessed.allowed
    assert "assess which Tasks" in unassessed.reason

    resumed = RunStateMachine.check(
        RunStatus.WAITING_FOR_USER,
        RunStatus.RUNNING,
        RunTransitionContext(
            information_requests_answered=True, clarification_impact_assessed=True
        ),
    )
    assert resumed.allowed


def test_intake_path_out_of_waiting_for_user_is_unchanged() -> None:
    check = RunStateMachine.check(
        RunStatus.WAITING_FOR_USER, RunStatus.PLANNED, RunTransitionContext(profile_confirmed=True)
    )
    assert check.allowed
