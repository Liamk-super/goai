from __future__ import annotations

from uuid import uuid4

import pytest

from launchscope_orchestrator.agentteams_bridge import AgentTeamsBridge, BridgePolicyError


class Directory:
    def agent_for_mxid(self, mxid: str) -> str | None:
        return {"@product:agentteams.local": "product-engineering"}.get(mxid)


def test_dispatch_is_routed_only_to_the_frozen_team() -> None:
    tenant_id, run_id, task_id = uuid4(), uuid4(), uuid4()
    assignment = AgentTeamsBridge().assignment_from_dispatch(
        {
            "event_type": "evaluation.task.ready.v1", "tenant_id": str(tenant_id),
            "run_id": str(run_id), "task_id": str(task_id),
            "payload": {
                "team_name": "launchscope-potential-review", "manifest_sha256": "a" * 64,
                "agent_code": "evaluation-manager", "stage_code": "LEADER_PLANNING",
                "skill_ref": "launchscope-evaluation-manager-handoff-v1",
                "context_token": "signed-task-capability", "handoff_schema": {"type": "object"},
                "usage_policy": {"required": False},
                "research_policy": {"material_only": True, "external_tools_required": False},
            },
        }
    )
    assert assignment.run_id == run_id
    assert assignment.team_name == "launchscope-potential-review"
    assert assignment.body["tenant_id"] == str(tenant_id)
    assert assignment.body["task_id"] == str(task_id)
    assert assignment.body["context_token"] == "signed-task-capability"
    assert assignment.body["handoff_schema"] == {"type": "object"}
    assert assignment.body["research_policy"]["material_only"] is True


def test_dispatch_rejects_a_task_without_routing_schema_or_capability() -> None:
    with pytest.raises(BridgePolicyError, match="task assignment"):
        AgentTeamsBridge().assignment_from_dispatch({
            "event_type": "evaluation.task.ready.v1", "tenant_id": str(uuid4()),
            "run_id": str(uuid4()), "task_id": str(uuid4()),
            "payload": {"team_name": "launchscope-potential-review", "manifest_sha256": "a" * 64},
        })


def test_matrix_sender_run_task_and_evidence_are_fail_closed() -> None:
    run_id, task_id, evidence_id = uuid4(), uuid4(), uuid4()
    content = {
        "schema_version": "1.0", "tenant_id": str(uuid4()), "run_id": str(run_id), "task_id": str(task_id),
        "agent_code": "product-engineering", "status": "SUCCEEDED", "dimension": "PRODUCT_IMPLEMENTATION",
        "claims": [{"statement": "The public flow loads", "evidence_ids": [str(evidence_id)], "hypothesis": False}],
        "evidence_refs": [str(evidence_id)], "risk": "LOW", "confidence": 0.8,
        "needs_human_approval": False, "failure_class": None, "next_action": "Audit evidence",
    }
    event = {"event_id": "$event", "room_id": "!room", "sender": "@product:agentteams.local", "content": content}
    accepted = AgentTeamsBridge().accept_matrix_event(
        event, Directory(), expected_run_id=run_id, expected_task_id=task_id
    )
    assert accepted.handoff.agent_code == "product-engineering"
    with pytest.raises(BridgePolicyError, match="sender MXID"):
        AgentTeamsBridge().accept_matrix_event(
            {**event, "sender": "@forged:agentteams.local"},
            Directory(), expected_run_id=run_id, expected_task_id=task_id,
        )
    with pytest.raises(ValueError, match="Evidence IDs"):
        unsupported = {
            **content,
            "claims": [{"statement": "unsupported", "evidence_ids": [], "hypothesis": False}],
        }
        AgentTeamsBridge().accept_matrix_event(
            {**event, "content": unsupported},
            Directory(), expected_run_id=run_id, expected_task_id=task_id,
        )
