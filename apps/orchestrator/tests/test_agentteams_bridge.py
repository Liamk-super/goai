from __future__ import annotations

from uuid import uuid4

import pytest

from launchscope_orchestrator.agentteams_bridge import AgentTeamsBridge, BridgePolicyError


class Directory:
    def agent_for_mxid(self, mxid: str) -> str | None:
        return {
            "@product:agentteams.local": "product-engineering",
            "@user:agentteams.local": "user-evidence",
        }.get(mxid)


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
                "research_policy": {
                    "material_only": True,
                    "external_tools_required": False,
                    "browser_calls_per_task": 2,
                    "search_queries_per_task": 8,
                },
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
    instruction = assignment.body["instruction"]
    assert "schema_version 1.1, status NEEDS_INPUT" in instruction
    assert "for every other status, information_requests must be empty" in instruction
    assert "Do not exceed research_policy.browser_calls_per_task" in instruction
    assert "not an AgentTeams Project" in instruction
    assert "the only delegation trigger" in instruction
    assert "do not delegate Workers yourself" in instruction


def test_v4_manager_assignment_requires_manager_plan_transport() -> None:
    assignment = AgentTeamsBridge().assignment_from_dispatch(
        {
            "event_type": "evaluation.task.ready.v1",
            "tenant_id": str(uuid4()),
            "run_id": str(uuid4()),
            "task_id": str(uuid4()),
            "payload": {
                "team_name": "launchscope-potential-review",
                "manifest_sha256": "a" * 64,
                "agent_code": "evaluation-manager",
                "stage_code": "LEADER_PLANNING",
                "skill_ref": "launchscope-evaluation-manager-handoff-v1",
                "context_token": "signed-task-capability",
                "message_type": "ManagerPlanV1",
                "handoff_schema": {"type": "object", "required": ["plan_id"]},
                "usage_policy": {"required": True},
                "research_policy": {
                    "material_only": False,
                    "external_tools_required": True,
                    "authorized_urls": ["https://creatrades.com"],
                    "browser_calls_per_task": 2,
                    "search_queries_per_task": 8,
                },
            },
        }
    )

    assert assignment.body["message_type"] == "ManagerPlanV1"
    instruction = assignment.body["instruction"]
    assert '"message_type":"ManagerPlanV1"' in instruction
    assert '"document"' in instruction
    assert "under 8000 UTF-8 bytes" in instruction
    assert "mcporter call --server launchscope-context --tool launchscope-context.get.v1" in instruction
    assert "Do not list or probe MCP tools" in instruction
    assert "every Task deadline_seconds to 900" in instruction
    assert "Never call write_file" in instruction
    assert "delete the plan" in instruction
    assert "AgentHandoffV1" not in instruction


def test_v4_material_manager_plan_is_single_pass_and_does_not_use_files_or_shell_drafting() -> None:
    assignment = AgentTeamsBridge().assignment_from_dispatch(
        {
            "event_type": "evaluation.task.ready.v1",
            "tenant_id": str(uuid4()),
            "run_id": str(uuid4()),
            "task_id": str(uuid4()),
            "payload": {
                "team_name": "launchscope-potential-review",
                "manifest_sha256": "a" * 64,
                "agent_code": "evaluation-manager",
                "stage_code": "LEADER_PLANNING",
                "skill_ref": "launchscope-evaluation-manager-handoff-v1",
                "context_token": "signed-task-capability",
                "message_type": "ManagerPlanV2",
                "handoff_schema": {"type": "object", "required": ["plan_id"]},
                "usage_policy": {"required": True},
                "research_policy": {
                    "material_only": False,
                    "external_tools_required": True,
                    "authorized_urls": ["https://creatrades.com"],
                    "browser_calls_per_task": 2,
                    "search_queries_per_task": 8,
                },
            },
        }
    )

    instruction = assignment.body["instruction"]
    assert "mcporter call --server launchscope-context --tool launchscope-context.get.v2" in instruction
    assert "Do not list or probe MCP tools" in instruction
    assert "Every Task must contain at least one material_scope" in instruction
    assert "every Task deadline_seconds to 3600" in instruction
    assert "After that single context result, immediately return" in instruction
    assert "Never call write_file" in instruction
    assert "use shell commands to draft, validate, measure, print, move, or delete the plan" in instruction


def test_v4_specialist_caps_research_and_requires_exact_evidence_ref_subset() -> None:
    assignment = AgentTeamsBridge().assignment_from_dispatch(
        {
            "event_type": "evaluation.task.ready.v1",
            "tenant_id": str(uuid4()),
            "run_id": str(uuid4()),
            "task_id": str(uuid4()),
            "payload": {
                "team_name": "launchscope-potential-review",
                "manifest_sha256": "a" * 64,
                "agent_code": "business-investment",
                "stage_code": "DOMAIN_REVIEW",
                "skill_ref": "business-investment-assessment",
                "context_token": "signed-task-capability",
                "message_type": "AgentHandoffV3",
                "handoff_schema": {"type": "object"},
                "usage_policy": {"required": True},
                "research_policy": {
                    "material_only": False,
                    "external_tools_required": True,
                    "authorized_urls": ["https://creatrades.com"],
                    "browser_calls_per_task": 2,
                    "search_queries_per_task": 8,
                },
            },
        }
    )

    instruction = assignment.body["instruction"]
    assert "at most two browser/search calls total" in instruction
    assert "no more than one returned content_ref" in instruction
    assert "claim under 300 UTF-8 bytes" in instruction
    assert "Never call write_file" in instruction
    assert "finding.evidence_refs value a subset" in instruction
    assert "never cite a ref omitted from document.evidence_refs" in instruction
    assert "character-for-character" in instruction
    assert "never reconstruct, abbreviate, or remove UUID hyphens" in instruction


def test_v4_targeted_remediation_requires_a_new_finding_identifier() -> None:
    assignment = AgentTeamsBridge().assignment_from_dispatch(
        {
            "event_type": "evaluation.task.ready.v1",
            "tenant_id": str(uuid4()),
            "run_id": str(uuid4()),
            "task_id": str(uuid4()),
            "payload": {
                "team_name": "launchscope-potential-review",
                "manifest_sha256": "a" * 64,
                "agent_code": "product-engineering",
                "stage_code": "TARGETED_REMEDIATION",
                "skill_ref": "browser-product-audit",
                "context_token": "signed-task-capability",
                "message_type": "AgentHandoffV3",
                "handoff_schema": {"type": "object"},
                "usage_policy": {"required": True},
                "research_policy": {
                    "material_only": False,
                    "external_tools_required": True,
                    "authorized_urls": ["https://creatrades.com"],
                    "browser_calls_per_task": 2,
                    "search_queries_per_task": 8,
                },
            },
        }
    )

    instruction = assignment.body["instruction"]
    assert "new UUID" in instruction
    assert "Never reuse the source finding_id" in instruction


def test_specialist_assignment_forces_configured_mcp_instead_of_builtin_browser() -> None:
    assignment = AgentTeamsBridge().assignment_from_dispatch({
        "event_type": "evaluation.task.ready.v1", "tenant_id": str(uuid4()),
        "run_id": str(uuid4()), "task_id": str(uuid4()),
        "payload": {
            "team_name": "launchscope-potential-review", "manifest_sha256": "a" * 64,
            "agent_code": "product-engineering", "stage_code": "DOMAIN_REVIEW",
            "skill_ref": "browser-product-audit", "context_token": "signed-task-capability",
            "handoff_schema": {"type": "object"}, "usage_policy": {"required": False},
            "research_policy": {
                "material_only": False, "external_tools_required": True,
                "authorized_urls": ["https://creatrades.com"],
                "browser_calls_per_task": 2, "search_queries_per_task": 8,
            },
        },
    })
    instruction = assignment.body["instruction"]
    assert "browser-audit.browser-audit.v1" in instruction
    assert "Never use browser_use" in instruction


def test_specialist_assignment_preserves_explicit_clarification_gate() -> None:
    assignment = AgentTeamsBridge().assignment_from_dispatch({
        "event_type": "evaluation.task.ready.v1", "tenant_id": str(uuid4()),
        "run_id": str(uuid4()), "task_id": str(uuid4()),
        "payload": {
            "team_name": "launchscope-potential-review", "manifest_sha256": "a" * 64,
            "agent_code": "user-evidence", "stage_code": "DOMAIN_REVIEW",
            "skill_ref": "user-evidence-analysis", "context_token": "signed-task-capability",
            "handoff_schema": {"type": "object"}, "usage_policy": {"required": False},
            "research_policy": {
                "material_only": False, "external_tools_required": True,
                "authorized_urls": ["https://creatrades.com"],
                "browser_calls_per_task": 2, "search_queries_per_task": 8,
            },
        },
    })

    instruction = assignment.body["instruction"]
    assert "explicitly marks a fact as missing or undetermined" in instruction
    assert "stop before browser/search and return NEEDS_INPUT" in instruction
    assert "do not replace the missing fact with UNKNOWN and continue" in instruction


def test_auditor_assignment_locks_immutable_finding_identifiers() -> None:
    assignment = AgentTeamsBridge().assignment_from_dispatch({
        "event_type": "evaluation.task.ready.v1", "tenant_id": str(uuid4()),
        "run_id": str(uuid4()), "task_id": str(uuid4()),
        "payload": {
            "team_name": "launchscope-potential-review", "manifest_sha256": "a" * 64,
            "agent_code": "evidence-auditor", "stage_code": "EVIDENCE_AUDIT",
            "skill_ref": "evidence-grounding-audit", "context_token": "signed-task-capability",
            "message_type": "AuditResultV3",
            "handoff_schema": {"type": "object"}, "usage_policy": {"required": True},
            "research_policy": {
                "material_only": False, "external_tools_required": True,
                "authorized_urls": ["https://creatrades.com"],
                "browser_calls_per_task": 2, "search_queries_per_task": 8,
            },
        },
    })

    instruction = assignment.body["instruction"]
    assert "audit_identity_lock item in ordinal order" in instruction
    assert "character-for-character" in instruction
    assert "never reconstruct, abbreviate, or alter either identifier" in instruction


@pytest.mark.parametrize("message_type", ["AuditResultV4", "ManagerSynthesisV2"])
def test_v6_report_transports_are_supported(message_type: str) -> None:
    agent_code = "evidence-auditor" if message_type == "AuditResultV4" else "evaluation-manager"
    assignment = AgentTeamsBridge().assignment_from_dispatch({
        "event_type": "evaluation.task.ready.v1", "tenant_id": str(uuid4()),
        "run_id": str(uuid4()), "task_id": str(uuid4()),
        "payload": {
            "team_name": "launchscope-potential-review", "manifest_sha256": "a" * 64,
            "agent_code": agent_code,
            "agent_contract_generation": "v6",
            "stage_code": "EVIDENCE_AUDIT" if message_type == "AuditResultV4" else "SUPERVISOR_SYNTHESIS",
            "skill_ref": "evidence-grounding-audit" if message_type == "AuditResultV4" else "manager-synthesis",
            "context_token": "signed-task-capability", "message_type": message_type,
            "handoff_schema": {"type": "object"}, "usage_policy": {"required": False},
            "research_policy": {"material_only": True},
        },
    })

    instruction = assignment.body["instruction"]
    assert message_type in instruction
    if message_type == "AuditResultV4":
        assert '"specialist_report":<SpecialistReportDocumentV2>' in instruction
        assert "schema_version 4.0" in instruction
    else:
        assert "deterministic Decision unchanged" in instruction
        assert "BACKGROUND Citations never establish Claim strength" in instruction


@pytest.mark.parametrize("message_type", ["AgentHandoffV3", "AgentHandoffV4"])
def test_v6_domain_assignment_requires_inline_specialist_report(message_type: str) -> None:
    assignment = AgentTeamsBridge().assignment_from_dispatch({
        "event_type": "evaluation.task.ready.v1", "tenant_id": str(uuid4()),
        "run_id": str(uuid4()), "task_id": str(uuid4()),
        "payload": {
            "team_name": "launchscope-potential-review", "manifest_sha256": "a" * 64,
            "agent_code": "product-engineering", "agent_contract_generation": "v6",
            "stage_code": "DOMAIN_REVIEW", "skill_ref": "product-technical-audit",
            "context_token": "signed-task-capability", "message_type": message_type,
            "handoff_schema": {"type": "object"}, "usage_policy": {"required": False},
            "research_policy": {"material_only": True},
        },
    })

    instruction = assignment.body["instruction"]
    assert '"specialist_report":<SpecialistReportDocumentV2>' in instruction
    assert "report_ref to null" in instruction
    assert "180000 UTF-8 bytes" in instruction
    assert (
        "python skills/launchscope-product-engineering-handoff-v3/scripts/launchscope_mcp_call.py "
        "--read-required-materials"
    ) in instruction
    assert "Do not transcribe context_token" in instruction
    assert "runtime_context" in instruction
    assert "project_id, product_version_id, and product_title" in instruction
    assert "runtime_context.report_preferences.locale" in instruction
    assert "all user-visible prose" in instruction
    assert "write_file exactly once" in instruction
    assert "Never run rm or mv" in instruction
    assert "at least eight claims" in instruction


def test_v6_user_assignment_uses_the_canonical_user_report_runner() -> None:
    assignment = AgentTeamsBridge().assignment_from_dispatch({
        "event_type": "evaluation.task.ready.v1", "tenant_id": str(uuid4()),
        "run_id": str(uuid4()), "task_id": str(uuid4()),
        "payload": {
            "team_name": "launchscope-potential-review", "manifest_sha256": "a" * 64,
            "agent_code": "user-evidence", "agent_contract_generation": "v6",
            "stage_code": "DOMAIN_REVIEW", "skill_ref": "user-validation-designer",
            "context_token": "signed-task-capability", "message_type": "AgentHandoffV3",
            "handoff_schema": {"type": "object"}, "usage_policy": {"required": False},
            "research_policy": {"material_only": True},
        },
    })

    instruction = assignment.body["instruction"]
    assert "skills/user-validation-designer/runner/report-cli.mjs" in instruction
    assert "manual fallback" in instruction


def test_v6_auditor_and_synthesis_require_the_frozen_report_locale() -> None:
    for agent_code, message_type, stage_code in (
        ("evidence-auditor", "AuditResultV4", "EVIDENCE_AUDIT"),
        ("evaluation-manager", "ManagerSynthesisV2", "SUPERVISOR_SYNTHESIS"),
    ):
        assignment = AgentTeamsBridge().assignment_from_dispatch({
            "event_type": "evaluation.task.ready.v1",
            "tenant_id": str(uuid4()),
            "run_id": str(uuid4()),
            "task_id": str(uuid4()),
            "payload": {
                "team_name": "launchscope-potential-review",
                "manifest_sha256": "a" * 64,
                "agent_code": agent_code,
                "agent_contract_generation": "v6",
                "stage_code": stage_code,
                "skill_ref": "evidence-grounding-audit" if agent_code == "evidence-auditor" else "manager-synthesis",
                "context_token": "signed-task-capability",
                "message_type": message_type,
                "handoff_schema": {"type": "object"},
                "usage_policy": {"required": False},
                "research_policy": {"material_only": True},
            },
        })

        assert "report_preferences.locale" in assignment.body["instruction"]
        assert "all user-visible prose" in assignment.body["instruction"]


def test_v2_handoff_rejects_a_full_report_sized_matrix_payload() -> None:
    run_id, task_id = uuid4(), uuid4()
    content = {
        "schema_version": "2.0",
        "tenant_id": str(uuid4()),
        "run_id": str(run_id),
        "task_id": str(task_id),
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
        "next_action": "x" * 40_000,
        "audit_results": [],
        "information_requests": [],
        "skill_result_ref": str(uuid4()),
        "skill_result_sha256": "a" * 64,
        "validation_mode": "first_validation",
    }
    event = {"event_id": "$event", "room_id": "!room", "sender": "@user:agentteams.local", "content": content}

    with pytest.raises(BridgePolicyError, match="narrow Matrix transport budget"):
        AgentTeamsBridge().accept_matrix_event(
            event,
            Directory(),
            expected_run_id=run_id,
            expected_task_id=task_id,
        )


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
