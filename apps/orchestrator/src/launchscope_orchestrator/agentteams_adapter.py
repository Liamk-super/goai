"""Transport-neutral view of the fixed AgentTeams hierarchy for one Run."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .manifest_loader import AGENT_CODES, MANAGER_CODE, AgentIdentityContract


class AgentTeamsError(ValueError):
    """The requested team composition violates the 1+5 fixed topology."""


@dataclass(frozen=True, slots=True)
class AgentTeam:
    run_id: UUID
    team_id: str
    manager: AgentIdentityContract
    workers: tuple[AgentIdentityContract, ...]

    @property
    def members(self) -> tuple[AgentIdentityContract, ...]:
        return (self.manager, *self.workers)


class AgentTeamsAdapter:
    """Creates a declarative team; it has no business-state write methods."""

    def create_team(self, run_id: UUID, contracts: tuple[AgentIdentityContract, ...]) -> AgentTeam:
        by_code = {contract.code: contract for contract in contracts}
        if set(by_code) != AGENT_CODES or len(by_code) != len(contracts):
            raise AgentTeamsError("a Run must use exactly the fixed 1+5 Agent identities")
        manager = by_code[MANAGER_CODE]
        if manager.role != "manager":
            raise AgentTeamsError("the fixed Manager role is required")
        workers = tuple(contract for code, contract in sorted(by_code.items()) if code != MANAGER_CODE)
        if any(worker.role != "specialist" for worker in workers):
            raise AgentTeamsError("all workers must have specialist role")
        return AgentTeam(run_id=run_id, team_id=f"run:{run_id}:agent-team", manager=manager, workers=workers)


__all__ = ["AgentTeam", "AgentTeamsAdapter", "AgentTeamsError"]
