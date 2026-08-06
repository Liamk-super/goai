"""Strict RocketMQ-to-AgentTeams and Matrix-to-control-plane bridge contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from .manifest_loader import AGENT_CODES


class BridgePolicyError(ValueError):
    """A transport message cannot be tied to a frozen Run/Task/Agent identity."""


class ClaimV1(BaseModel):
    statement: str = Field(min_length=1, max_length=10_000)
    evidence_ids: list[UUID]
    hypothesis: bool
    region: str | None = Field(default=None, max_length=100)
    fetched_at: str | None = None
    valid_until: str | None = None
    trend_signal: Literal["FAVORABLE", "NEUTRAL", "ADVERSE", "UNKNOWN"] | None = None

    @field_validator("evidence_ids")
    @classmethod
    def evidence_required_for_facts(cls, value: list[UUID], info: ValidationInfo) -> list[UUID]:
        return value


class AuditResultV1(BaseModel):
    finding_id: UUID
    decision: Literal["ACCEPTED", "DOWNGRADED", "REJECTED", "NEEDS_MORE_EVIDENCE"]
    reason: str = Field(min_length=1, max_length=2000)


class AgentHandoffV1(BaseModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    tenant_id: UUID
    run_id: UUID
    task_id: UUID
    agent_code: str
    status: Literal["SUCCEEDED", "BLOCKED", "FAILED"]
    dimension: str
    claims: list[ClaimV1]
    evidence_refs: list[UUID]
    risk: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    confidence: float = Field(ge=0, le=1)
    needs_human_approval: bool
    failure_class: str | None = None
    next_action: str = Field(min_length=1, max_length=2000)
    audit_results: list[AuditResultV1] = Field(default_factory=list)

    @field_validator("agent_code")
    @classmethod
    def fixed_agent(cls, value: str) -> str:
        if value not in AGENT_CODES:
            raise ValueError("agent_code is not in the frozen 1+5 catalog")
        return value

    def model_post_init(self, __context: object) -> None:
        for claim in self.claims:
            if not claim.hypothesis and not claim.evidence_ids:
                raise ValueError("non-hypothesis Claims require Evidence IDs")
        referenced = {value for claim in self.claims for value in claim.evidence_ids}
        if not referenced.issubset(set(self.evidence_refs)):
            raise ValueError("Claim evidence_ids must be present in evidence_refs")


class MatrixSenderDirectory(Protocol):
    def agent_for_mxid(self, mxid: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ManagerAssignment:
    run_id: UUID
    team_name: str
    body: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class AcceptedMatrixEvent:
    matrix_event_id: str
    room_id: str
    sender_mxid: str
    payload_sha256: str
    handoff: AgentHandoffV1


class AgentTeamsBridge:
    TEAM_NAME = "launchscope-potential-review"

    def assignment_from_dispatch(self, event: Mapping[str, object]) -> ManagerAssignment:
        if event.get("event_type") != "evaluation.task.ready.v1":
            raise BridgePolicyError("Bridge accepts only the versioned Task-ready event")
        payload = event.get("payload")
        if not isinstance(payload, Mapping) or payload.get("team_name") != self.TEAM_NAME:
            raise BridgePolicyError("dispatch event does not target the frozen LaunchScope Team")
        run_id = UUID(str(event.get("run_id")))
        required = (
            "agent_code", "stage_code", "skill_ref", "context_token", "handoff_schema",
            "usage_policy", "research_policy",
        )
        if not event.get("tenant_id") or not event.get("task_id") or any(not payload.get(key) for key in required):
            raise BridgePolicyError("task assignment lacks routing, schema, capability, or policy")
        return ManagerAssignment(
            run_id=run_id,
            team_name=self.TEAM_NAME,
            body={
                "schema_version": "1.0",
                "tenant_id": str(event.get("tenant_id")),
                "run_id": str(run_id),
                "task_id": str(event.get("task_id")),
                "team_name": self.TEAM_NAME,
                "manifest_sha256": str(payload.get("manifest_sha256")),
                "agent_code": str(payload.get("agent_code")),
                "stage_code": str(payload.get("stage_code")),
                "skill_ref": str(payload.get("skill_ref")),
                "context_token": str(payload.get("context_token")),
                "handoff_schema": payload.get("handoff_schema"),
                "usage_policy": payload.get("usage_policy"),
                "research_policy": payload.get("research_policy"),
                "instruction": (
                    "Use the task capability with the assigned read-only MCP tools. "
                    "If research_policy.material_only is true, do not require browser/search: use registered material "
                    "Evidence and mark unsupported assertions as hypotheses. Return exactly one AgentHandoffV1 JSON "
                    "object, including structured BLOCKED/FAILED results."
                ),
            },
        )

    def accept_matrix_event(
        self,
        event: Mapping[str, object],
        directory: MatrixSenderDirectory,
        *,
        expected_run_id: UUID,
        expected_task_id: UUID,
    ) -> AcceptedMatrixEvent:
        event_id = str(event.get("event_id", ""))
        room_id = str(event.get("room_id", ""))
        sender = str(event.get("sender", ""))
        content = event.get("content")
        if not event_id or not room_id or not sender or not isinstance(content, Mapping):
            raise BridgePolicyError("Matrix event lacks immutable identity fields")
        agent_code = directory.agent_for_mxid(sender)
        if agent_code is None:
            raise BridgePolicyError("Matrix sender MXID is not a reconciled Agent identity")
        handoff = AgentHandoffV1.model_validate(content)
        if handoff.agent_code != agent_code:
            raise BridgePolicyError("Matrix sender MXID does not match AgentHandoffV1.agent_code")
        if handoff.run_id != expected_run_id or handoff.task_id != expected_task_id:
            raise BridgePolicyError("Matrix handoff Run/Task does not match the durable assignment")
        digest = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        return AcceptedMatrixEvent(event_id, room_id, sender, digest, handoff)


__all__ = [
    "AcceptedMatrixEvent", "AgentHandoffV1", "AgentTeamsBridge", "AuditResultV1", "BridgePolicyError",
    "ClaimV1", "ManagerAssignment", "MatrixSenderDirectory",
]
