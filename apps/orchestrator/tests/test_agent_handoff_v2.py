from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from launchscope_orchestrator.agentteams_bridge import AgentHandoffV2, AuditResultV2


def _user_handoff() -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "tenant_id": str(uuid4()),
        "run_id": str(uuid4()),
        "task_id": str(uuid4()),
        "dispatch_epoch": 0,
        "agent_code": "user-evidence",
        "status": "SUCCEEDED",
        "dimension": "USER_USAGE",
        "claims": [],
        "evidence_refs": [],
        "risk": "MEDIUM",
        "confidence": 0.5,
        "needs_human_approval": False,
        "failure_class": None,
        "next_action": "Evidence Auditor should calibrate the referenced result.",
        "audit_results": [],
        "information_requests": [],
        "skill_result_ref": str(uuid4()),
        "skill_result_sha256": "a" * 64,
        "validation_mode": "first_validation",
    }


def test_user_handoff_v2_requires_only_a_narrow_integrity_bound_reference() -> None:
    handoff = AgentHandoffV2.model_validate(_user_handoff())

    assert handoff.skill_result_sha256 == "a" * 64
    assert "result" not in handoff.model_dump()


def test_user_handoff_v2_rejects_full_report_payloads() -> None:
    payload = _user_handoff()
    payload["full_report"] = {"private": "body"}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentHandoffV2.model_validate(payload)


def test_user_handoff_v2_requires_ref_hash_and_mode_as_one_binding() -> None:
    payload = _user_handoff()
    payload["skill_result_sha256"] = None

    with pytest.raises(ValidationError, match="supplied together"):
        AgentHandoffV2.model_validate(payload)


def test_audit_result_v2_requires_kb_rules_and_an_exact_component_total() -> None:
    payload = {
        "finding_id": str(uuid4()),
        "decision": "DOWNGRADED",
        "reason": "Simulation cannot establish real user behavior.",
        "rule_ids": ["KB-EVD-D02"],
        "evidence_ids": [],
        "score_components": {
            "evidence_strength": 10,
            "source_reliability": 10,
            "freshness": 10,
            "reasoning_quality": 10,
            "total": 41,
        },
        "flags": ["SIMULATION_ONLY"],
    }

    with pytest.raises(ValidationError, match="total must equal"):
        AuditResultV2.model_validate(payload)
    payload["score_components"]["total"] = 40
    assert AuditResultV2.model_validate(payload).rule_ids == ["KB-EVD-D02"]
