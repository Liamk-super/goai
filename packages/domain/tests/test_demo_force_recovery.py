from launchscope_domain import FailureClass, RunStateMachine, RunStatus, TaskStatus
from launchscope_domain.services.run_state_machine import RunTransitionContext
from launchscope_domain.services.task_dag import TaskStateMachine


def test_demo_force_resume_allows_submission_unknown_run() -> None:
    ordinary = RunStateMachine.check(
        RunStatus.NEEDS_ATTENTION,
        RunStatus.RUNNING,
        RunTransitionContext(human_resume=True, failure_class=FailureClass.SUBMISSION_UNKNOWN),
    )
    forced = RunStateMachine.check(
        RunStatus.NEEDS_ATTENTION,
        RunStatus.RUNNING,
        RunTransitionContext(
            human_resume=True,
            failure_class=FailureClass.SUBMISSION_UNKNOWN,
            demo_force_resume=True,
        ),
    )

    assert not ordinary.allowed
    assert forced.allowed


def test_demo_force_resume_requeues_attention_and_running_tasks_only_when_explicit() -> None:
    assert not TaskStateMachine.check(TaskStatus.NEEDS_ATTENTION, TaskStatus.PENDING).allowed
    assert not TaskStateMachine.check(TaskStatus.RUNNING, TaskStatus.PENDING).allowed
    assert TaskStateMachine.check(
        TaskStatus.NEEDS_ATTENTION,
        TaskStatus.PENDING,
        demo_force_resume=True,
    ).allowed
    assert TaskStateMachine.check(
        TaskStatus.RUNNING,
        TaskStatus.PENDING,
        demo_force_resume=True,
    ).allowed
