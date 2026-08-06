"""Matrix adapter that validates handoffs and only emits control-plane commands."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .agentteams_adapter import AgentTeam
from .handoff import MatrixHandoff
from .manifest_loader import AgentIdentityContract


class MatrixAdapterError(ValueError):
    """A Matrix worker attempted an unauthorized handoff."""


@dataclass(frozen=True, slots=True)
class ControlPlaneCommand:
    command_type: str
    task_id: str
    requested_status: str
    reason: str
    failure_class: str | None


@dataclass(frozen=True, slots=True)
class MatrixRoom:
    """A task-scoped room: Manager leads, one specialist works, Human approves."""

    room_id: str
    task_id: UUID
    leader_agent: str
    worker_agent: str
    human_approval_principal: str = "human"


class MatrixAdapter:
    """No direct Run/Task/Memory/Report mutation exists at this boundary."""

    def accept(self, handoff: MatrixHandoff, sender: AgentIdentityContract) -> MatrixHandoff:
        if handoff.sender_agent != sender.code:
            raise MatrixAdapterError("Matrix sender does not match the fixed Agent identity")
        if handoff.kind == "FINDING" and "finding" not in sender.outputs:
            raise MatrixAdapterError("Agent identity is not permitted to submit Findings")
        if handoff.kind == "STATE_CHANGE_REQUEST" and "state_change_request" not in sender.outputs:
            raise MatrixAdapterError("Agent identity is not permitted to request status changes")
        return handoff

    def create_room(self, team: AgentTeam, *, task_id: UUID, worker_agent: str) -> MatrixRoom:
        if worker_agent not in {worker.code for worker in team.workers}:
            raise MatrixAdapterError("Matrix worker must be a specialist in the Run's fixed AgentTeam")
        return MatrixRoom(
            room_id=f"run:{team.run_id}:task:{task_id}:matrix",
            task_id=task_id,
            leader_agent=team.manager.code,
            worker_agent=worker_agent,
        )

    def to_control_plane_command(self, handoff: MatrixHandoff) -> ControlPlaneCommand:
        if handoff.kind != "STATE_CHANGE_REQUEST" or handoff.requested_status is None:
            raise MatrixAdapterError("only a state-change request can become a control-plane command")
        reason = handoff.structured_result.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise MatrixAdapterError("state-change requests require a structured reason")
        return ControlPlaneCommand(
            command_type="REQUEST_TASK_STATE_CHANGE",
            task_id=str(handoff.task_id),
            requested_status=handoff.requested_status.value,
            reason=reason.strip(),
            failure_class=handoff.failure_class.value if handoff.failure_class else None,
        )


__all__ = ["ControlPlaneCommand", "MatrixAdapter", "MatrixAdapterError", "MatrixRoom"]
