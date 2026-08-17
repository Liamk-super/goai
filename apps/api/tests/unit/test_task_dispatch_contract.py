from __future__ import annotations

import pytest

from launchscope_api.modules.evaluation.task_dispatch import (
    _assignment_contract,
    _configured_agent_max_iters,
    _research_policy,
    provider_cost_mode,
)


def test_generation_v4_stages_dispatch_their_frozen_document_contracts() -> None:
    config = {"agent_contract_generation": "v4"}

    assert _assignment_contract(config, "LEADER_PLANNING")[0] == "ManagerPlanV1"
    assert _assignment_contract(config, "DOMAIN_REVIEW")[0] == "AgentHandoffV3"
    assert _assignment_contract(config, "EVIDENCE_AUDIT")[0] == "AuditResultV3"
    assert _assignment_contract(config, "SUPERVISOR_SYNTHESIS")[0] == "ManagerSynthesisV1"
    assert _assignment_contract(config, "LEADER_PLANNING")[1]["$id"].endswith("manager-plan.v1.json")


def test_generation_v6_dispatches_report_v22_contracts() -> None:
    config = {"agent_contract_generation": "v6"}

    assert _assignment_contract(config, "LEADER_PLANNING")[0] == "ManagerPlanV2"
    assert _assignment_contract(config, "DOMAIN_REVIEW")[0] == "AgentHandoffV3"
    assert _assignment_contract(config, "EVIDENCE_AUDIT")[0] == "AuditResultV4"
    assert _assignment_contract(config, "SUPERVISOR_SYNTHESIS")[0] == "ManagerSynthesisV2"


def test_generation_v6_recovery_expands_domain_handoff_contract_after_epoch_one() -> None:
    config = {"agent_contract_generation": "v6"}

    message_type, schema = _assignment_contract(config, "DOMAIN_REVIEW", dispatch_epoch=2)

    assert message_type == "AgentHandoffV4"
    assert schema["$id"].endswith("agent-handoff.v4.json")
    assert schema["properties"]["dispatch_epoch"]["maximum"] >= 2


def test_delivery_model_budget_covers_long_running_worker_iteration_limit(monkeypatch) -> None:
    limits = {"agent_iterations_by_agent": {"product-engineering": 30, "user-evidence": 32}}
    monkeypatch.setenv("LAUNCHSCOPE_COPAW_MAX_ITERS", "128")
    monkeypatch.setenv("LAUNCHSCOPE_USER_COPAW_MAX_ITERS", "256")

    assert _configured_agent_max_iters(limits, "product-engineering") == 128
    assert _configured_agent_max_iters(limits, "user-evidence") == 256


def test_provider_cost_mode_defaults_to_token_only_and_rejects_unknown_values(monkeypatch) -> None:
    monkeypatch.delenv("LAUNCHSCOPE_PROVIDER_COST_MODE", raising=False)
    assert provider_cost_mode() == "TOKEN_ONLY"

    monkeypatch.setenv("LAUNCHSCOPE_PROVIDER_COST_MODE", "exact")
    assert provider_cost_mode() == "EXACT"

    monkeypatch.setenv("LAUNCHSCOPE_PROVIDER_COST_MODE", "invoice-later")
    with pytest.raises(ValueError, match="EXACT or TOKEN_ONLY"):
        provider_cost_mode()


def test_research_policy_is_scoped_to_each_tasks_tool_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MATERIAL_ONLY", "false")
    limits = {"browser_calls_per_task": 2, "search_queries": 8}

    audit = _research_policy(["launchscope-context.get.v1"], limits, ["https://creatrades.com"])
    assert audit == {
        "material_only": True,
        "external_tools_required": False,
        "authorized_urls": ["https://creatrades.com"],
        "browser_calls_per_task": 0,
        "search_queries_per_task": 0,
    }

    domain = _research_policy(
        ["launchscope-context.get.v1", "browser-audit.v1", "public-research-search.v1"],
        limits,
        ["https://creatrades.com"],
    )
    assert domain["material_only"] is False
    assert domain["external_tools_required"] is True
    assert domain["browser_calls_per_task"] == 2
    assert domain["search_queries_per_task"] == 8
