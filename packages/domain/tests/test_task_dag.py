from __future__ import annotations

from uuid import uuid4

import pytest

from launchscope_domain import (
    CycleDetectedError,
    FailureClass,
    InvalidTransitionError,
    RetryNotPermittedError,
    StageCode,
    Task,
    TaskDAG,
    TaskStatus,
)


def test_task_dag_is_topologically_sorted_and_dependency_gated() -> None:
    run_id = uuid4()
    first = Task.create(run_id, StageCode.PLANNING)
    second = Task.create(run_id, StageCode.PLANNING, dependencies=(first.task_id,))
    dag = TaskDAG((second, first))
    assert dag.topological_order() == (first.task_id, second.task_id)
    assert dag.ready_tasks() == (first,)

    first.lease("lease").start_running().succeed()
    assert dag.ready_tasks() == (second,)


def test_task_dag_detects_cycles() -> None:
    run_id = uuid4()
    first_id, second_id = uuid4(), uuid4()
    first = Task(first_id, run_id, StageCode.PLANNING, dependencies=(second_id,))
    second = Task(second_id, run_id, StageCode.PLANNING, dependencies=(first_id,))
    with pytest.raises(CycleDetectedError):
        TaskDAG((first, second)).topological_order()


def test_task_retry_is_limited_to_one_schema_correction_and_blocks_unknown() -> None:
    task = Task.create(uuid4(), StageCode.PLANNING)
    task.lease("lease").start_running().fail(FailureClass.VALIDATION, "invalid output")
    task.retry()
    task.lease("lease-2").start_running().fail(FailureClass.VALIDATION, "invalid output again")
    with pytest.raises(RetryNotPermittedError):
        task.retry()

    unknown = Task.create(uuid4(), StageCode.PLANNING)
    unknown.lease("lease").start_running().fail(FailureClass.SUBMISSION_UNKNOWN, "unknown")
    assert unknown.status is TaskStatus.NEEDS_ATTENTION
    with pytest.raises(RetryNotPermittedError):
        unknown.retry()


def test_expired_lease_cannot_be_retried_without_known_no_side_effect_status() -> None:
    task = Task.create(uuid4(), StageCode.PLANNING)
    task.lease("lease").expire_lease()
    with pytest.raises(InvalidTransitionError):
        task.retry(status_known=False, no_side_effect=False)
    task.retry(status_known=True, no_side_effect=True)
    assert task.status is TaskStatus.PENDING
