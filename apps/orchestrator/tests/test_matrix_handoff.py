from __future__ import annotations

from uuid import uuid4

import pytest

from launchscope_domain import TaskStatus
from launchscope_orchestrator.agentteams_adapter import AgentTeamsAdapter
from launchscope_orchestrator.handoff import HandoffValidationError, MatrixHandoff
from launchscope_orchestrator.manifest_loader import AgentManifestLoader
from launchscope_orchestrator.matrix_adapter import MatrixAdapter, MatrixAdapterError


def test_matrix_finding_handoff_is_structured_evidence_only() -> None:
    sender = next(contract for contract in AgentManifestLoader().load_all() if contract.code == "product-engineering")
    handoff = MatrixHandoff(
        task_id=uuid4(),
        sender_agent=sender.code,
        kind="FINDING",
        structured_result={"claim": "onboarding blocks completion"},
        evidence_uris=("evidence://7a6af978-0693-4ea0-922d-8ec67c7d2952",),
        risk="MEDIUM",
        confidence=0.8,
        approval_required=False,
        failure_class=None,
        requested_status=None,
    )

    assert MatrixAdapter().accept(handoff, sender) is handoff
    team = AgentTeamsAdapter().create_team(uuid4(), AgentManifestLoader().load_all())
    room = MatrixAdapter().create_room(team, task_id=handoff.task_id, worker_agent=sender.code)
    assert (room.leader_agent, room.worker_agent, room.human_approval_principal) == (
        "evaluation-manager",
        "product-engineering",
        "human",
    )
    with pytest.raises(HandoffValidationError, match="may not carry"):
        MatrixHandoff(
            task_id=uuid4(),
            sender_agent=sender.code,
            kind="FINDING",
            structured_result={"chat_history": "private transcript"},
            evidence_uris=("evidence://7a6af978-0693-4ea0-922d-8ec67c7d2952",),
            risk="LOW",
            confidence=1.0,
            approval_required=False,
            failure_class=None,
            requested_status=None,
        )


def test_matrix_state_change_becomes_command_not_mutation() -> None:
    sender = next(contract for contract in AgentManifestLoader().load_all() if contract.code == "user-evidence")
    handoff = MatrixHandoff(
        task_id=uuid4(),
        sender_agent=sender.code,
        kind="STATE_CHANGE_REQUEST",
        structured_result={"reason": "required source is unavailable"},
        evidence_uris=(),
        risk="HIGH",
        confidence=0.9,
        approval_required=True,
        failure_class=None,
        requested_status=TaskStatus.WAITING_FOR_APPROVAL,
    )

    command = MatrixAdapter().to_control_plane_command(MatrixAdapter().accept(handoff, sender))
    assert command.command_type == "REQUEST_TASK_STATE_CHANGE"
    assert command.requested_status == "WAITING_FOR_APPROVAL"
    with pytest.raises(MatrixAdapterError, match="only a state-change"):
        MatrixAdapter().to_control_plane_command(
            MatrixHandoff(
                task_id=uuid4(), sender_agent=sender.code, kind="FINDING", structured_result={"claim": "x"},
                evidence_uris=("evidence://a",), risk="LOW", confidence=0.1, approval_required=False,
                failure_class=None, requested_status=None,
            )
        )
