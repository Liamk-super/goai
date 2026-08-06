"""Dynamic task DAG validation and Task status guards."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from ..enums import FailureClass, TaskStatus
from ..errors import CycleDetectedError, InvalidTransitionError, ValidationError


class TaskLike(Protocol):
    task_id: UUID
    dependencies: tuple[UUID, ...]
    status: TaskStatus
    required: bool
    evidence_required: bool
    evidence_ids: tuple[UUID, ...]
    success_condition: str


@dataclass(frozen=True, slots=True)
class TaskTransitionCheck:
    allowed: bool
    current: TaskStatus
    target: TaskStatus
    reason: str = ""
    code: str = ""


class TaskStateMachine:
    """Legal Task transitions, including the explicit retry deny-list."""

    _transitions: dict[TaskStatus, frozenset[TaskStatus]] = {
        TaskStatus.PENDING: frozenset({TaskStatus.BLOCKED, TaskStatus.LEASED}),
        TaskStatus.BLOCKED: frozenset({TaskStatus.PENDING}),
        TaskStatus.LEASED: frozenset({TaskStatus.RUNNING, TaskStatus.EXPIRED}),
        TaskStatus.RUNNING: frozenset(
            {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.NEEDS_ATTENTION,
                TaskStatus.WAITING_FOR_APPROVAL,
                TaskStatus.CANCELLED,
            }
        ),
        TaskStatus.WAITING_FOR_APPROVAL: frozenset({TaskStatus.RUNNING, TaskStatus.NEEDS_ATTENTION}),
        TaskStatus.EXPIRED: frozenset({TaskStatus.PENDING}),
        TaskStatus.FAILED: frozenset({TaskStatus.PENDING}),
        TaskStatus.SUCCEEDED: frozenset(),
        TaskStatus.NEEDS_ATTENTION: frozenset(),
        TaskStatus.CANCELLED: frozenset(),
    }

    @classmethod
    def check(
        cls,
        current: TaskStatus,
        target: TaskStatus,
        *,
        failure_class: FailureClass | None = None,
        known_status: bool = False,
        no_side_effect: bool = False,
        retry_available: bool = False,
        approval_valid: bool = False,
    ) -> TaskTransitionCheck:
        current_status = TaskStatus(current)
        target_status = TaskStatus(target)
        if target_status not in cls._transitions[current_status]:
            return TaskTransitionCheck(
                False,
                current_status,
                target_status,
                "transition is not in the Task transition table",
                "ILLEGAL_TRANSITION",
            )
        if (
            current_status is TaskStatus.RUNNING
            and target_status is TaskStatus.NEEDS_ATTENTION
            and failure_class not in {FailureClass.BUDGET, FailureClass.POLICY, FailureClass.SUBMISSION_UNKNOWN}
        ):
            return TaskTransitionCheck(
                False,
                current_status,
                target_status,
                "attention requires a fail-closed class",
                "FAILURE_CLASS_REQUIRED",
            )
        if (
            current_status is TaskStatus.WAITING_FOR_APPROVAL
            and target_status is TaskStatus.RUNNING
            and not approval_valid
        ):
            return TaskTransitionCheck(
                False,
                current_status,
                target_status,
                "approval is not valid",
                "APPROVAL_INVALID",
            )
        if current_status in {TaskStatus.FAILED, TaskStatus.EXPIRED} and target_status is TaskStatus.PENDING:
            if not retry_available:
                return TaskTransitionCheck(
                    False,
                    current_status,
                    target_status,
                    "retry policy has no remaining attempt",
                    "RETRY_EXHAUSTED",
                )
            if current_status is TaskStatus.EXPIRED and (not known_status or not no_side_effect):
                return TaskTransitionCheck(
                    False,
                    current_status,
                    target_status,
                    "an expired task is retryable only when no side effect occurred and status is known",
                    "EXPIRED_RETRY_UNSAFE",
                )
            if failure_class in {
                FailureClass.AUTHORIZATION,
                FailureClass.BUDGET,
                FailureClass.POLICY,
                FailureClass.SUBMISSION_UNKNOWN,
            }:
                return TaskTransitionCheck(
                    False,
                    current_status,
                    target_status,
                    "failure class is not retryable",
                    "RETRY_BLOCKED",
                )
        return TaskTransitionCheck(True, current_status, target_status)

    @classmethod
    def transition(
        cls,
        current: TaskStatus,
        target: TaskStatus,
        *,
        failure_class: FailureClass | None = None,
        known_status: bool = False,
        no_side_effect: bool = False,
        retry_available: bool = False,
        approval_valid: bool = False,
    ) -> TaskStatus:
        check = cls.check(
            current,
            target,
            failure_class=failure_class,
            known_status=known_status,
            no_side_effect=no_side_effect,
            retry_available=retry_available,
            approval_valid=approval_valid,
        )
        if not check.allowed:
            raise InvalidTransitionError(check.current, check.target, reason=check.reason, details={"code": check.code})
        return check.target


@dataclass(frozen=True, slots=True)
class TaskCompletion:
    """Structured output required before a task can become SUCCEEDED."""

    schema_valid: bool = True
    success_condition_met: bool = True
    evidence_ids: tuple[UUID, ...] = ()
    status_known: bool = True
    no_side_effect: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_ids", tuple(UUID(str(value)) for value in self.evidence_ids))


@dataclass(frozen=True, slots=True)
class DagValidation:
    valid: bool
    topological_order: tuple[UUID, ...]
    missing_dependencies: tuple[UUID, ...] = ()


class TaskDAG:
    """A deterministic dependency graph over a single Run."""

    def __init__(self, tasks: Iterable[TaskLike]) -> None:
        task_list = tuple(tasks)
        self._tasks: dict[UUID, TaskLike] = {}
        for task in task_list:
            if task.task_id in self._tasks:
                raise ValidationError("task_id must be unique in a DAG", details={"task_id": str(task.task_id)})
            self._tasks[task.task_id] = task

    @property
    def tasks(self) -> tuple[TaskLike, ...]:
        return tuple(self._tasks.values())

    def validate(self) -> DagValidation:
        missing = sorted(
            {
                dependency
                for task in self._tasks.values()
                for dependency in task.dependencies
                if dependency not in self._tasks
            },
            key=str,
        )
        if missing:
            return DagValidation(False, (), tuple(missing))

        indegree = {task_id: 0 for task_id in self._tasks}
        outgoing: dict[UUID, list[UUID]] = {task_id: [] for task_id in self._tasks}
        for task in self._tasks.values():
            for dependency in task.dependencies:
                indegree[task.task_id] += 1
                outgoing[dependency].append(task.task_id)

        ready = deque(sorted((task_id for task_id, degree in indegree.items() if degree == 0), key=str))
        order: list[UUID] = []
        while ready:
            task_id = ready.popleft()
            order.append(task_id)
            for dependent in sorted(outgoing[task_id], key=str):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
        if len(order) != len(self._tasks):
            cycle_nodes = tuple(sorted((task_id for task_id, degree in indegree.items() if degree > 0), key=str))
            raise CycleDetectedError(
                "task dependency graph contains a cycle",
                details={"task_ids": [str(task_id) for task_id in cycle_nodes]},
            )
        return DagValidation(True, tuple(order))

    def topological_order(self) -> tuple[UUID, ...]:
        result = self.validate()
        if not result.valid:
            raise ValidationError(
                "task dependency graph contains an unknown dependency",
                details={"task_ids": [str(task_id) for task_id in result.missing_dependencies]},
            )
        return result.topological_order

    def dependencies_satisfied(self, task_id: UUID | str) -> bool:
        task_uuid = UUID(str(task_id))
        task = self._tasks[task_uuid]
        return all(self._tasks[dependency].status is TaskStatus.SUCCEEDED for dependency in task.dependencies)

    def ready_tasks(self) -> tuple[TaskLike, ...]:
        self.validate()
        return tuple(
            task
            for task in self._tasks.values()
            if task.status is TaskStatus.PENDING and self.dependencies_satisfied(task.task_id)
        )

    def mark_blocked_tasks(self) -> tuple[UUID, ...]:
        """Move pending tasks with incomplete dependencies to BLOCKED."""

        blocked: list[UUID] = []
        for task in self._tasks.values():
            if (
                task.status is TaskStatus.PENDING
                and task.dependencies
                and not self.dependencies_satisfied(task.task_id)
            ):
                TaskStateMachine.transition(task.status, TaskStatus.BLOCKED)
                task.status = TaskStatus.BLOCKED
                blocked.append(task.task_id)
        return tuple(blocked)

    def unblock_ready_tasks(self) -> tuple[UUID, ...]:
        unblocked: list[UUID] = []
        for task in self._tasks.values():
            if task.status is TaskStatus.BLOCKED and self.dependencies_satisfied(task.task_id):
                TaskStateMachine.transition(task.status, TaskStatus.PENDING)
                task.status = TaskStatus.PENDING
                unblocked.append(task.task_id)
        return tuple(unblocked)

    def missing_required_evidence(self, task_id: UUID | str) -> bool:
        task = self._tasks[UUID(str(task_id))]
        return task.evidence_required and not task.evidence_ids

    def validate_completion(self, task_id: UUID | str, completion: TaskCompletion) -> None:
        task = self._tasks[UUID(str(task_id))]
        if not completion.schema_valid:
            raise ValidationError("task output schema is invalid", details={"task_id": str(task.task_id)})
        if not completion.success_condition_met:
            raise ValidationError("task success condition is not satisfied", details={"task_id": str(task.task_id)})
        if task.evidence_required and not completion.evidence_ids:
            raise ValidationError("required evidence is missing", details={"task_id": str(task.task_id)})

    def validate_for_dispatch(self, task_id: UUID | str) -> None:
        task = self._tasks[UUID(str(task_id))]
        if task.status is not TaskStatus.PENDING:
            raise InvalidTransitionError(task.status, TaskStatus.LEASED, reason="task is not pending")
        if not self.dependencies_satisfied(task.task_id):
            raise InvalidTransitionError(task.status, TaskStatus.LEASED, reason="task dependencies are not satisfied")


# A descriptive alias used by some application code.
TaskDag = TaskDAG
