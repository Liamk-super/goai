"""Control-plane ownership of Agent-proposed state changes.

This module deliberately translates a request into an auditable command.  It
does not receive an EvaluationRun, repository, Memory, or Report writer and
therefore cannot apply a Worker-originated mutation directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from launchscope_domain import FailureClass, TaskStatus


class StateChangeRequestError(ValueError):
    """An Agent's requested control-plane change is invalid."""


@dataclass(frozen=True, slots=True)
class StateChangeRequest:
    task_id: UUID
    requested_status: TaskStatus
    reason: str
    failure_class: FailureClass | None = None

    def __post_init__(self) -> None:
        if not self.reason.strip() or len(self.reason.strip()) > 1000:
            raise StateChangeRequestError("reason must be a non-empty string up to 1000 characters")


@dataclass(frozen=True, slots=True)
class PendingStateChangeCommand:
    task_id: UUID
    requested_status: TaskStatus
    reason: str
    failure_class: FailureClass | None
    source: str = "agent-matrix"


class StateChangeRequestApplication:
    """Translate, audit, and queue; an authorized controller applies later."""

    def to_pending_command(self, request: StateChangeRequest) -> PendingStateChangeCommand:
        return PendingStateChangeCommand(
            task_id=request.task_id,
            requested_status=request.requested_status,
            reason=request.reason.strip(),
            failure_class=request.failure_class,
        )


__all__ = [
    "PendingStateChangeCommand",
    "StateChangeRequest",
    "StateChangeRequestApplication",
    "StateChangeRequestError",
]
