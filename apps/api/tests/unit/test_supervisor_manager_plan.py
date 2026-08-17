from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from launchscope_api.modules.supervisor.planning_application import (
    ManagerPlanningApplication,
    ManagerPlanValidationError,
    ManagerPlanValidator,
    _normalized_task_budget_suggestions,
)


def test_report_v22_manifest_freezes_v6_contracts_and_profiles() -> None:
    manifest = ManagerPlanningApplication._run_manifest(
        "FULL_POTENTIAL",
        material_v2=True,
        report_v2=True,
    )

    assert manifest["schema_version"] == "6.0"
    assert manifest["architecture_generation"] == "supervisor-1p4-report-v22"
    assert manifest["agent_contract_generation"] == "v6"
    assert manifest["contracts"]["manager_synthesis"]["version"] == "v2"
    assert manifest["contracts"]["audit_result"]["version"] == "v4"
    assert manifest["score_profile"]["version"] == "2.0"
    assert manifest["report_profile"]["version"] == "2.0"
    assert manifest["limits"]["model_calls"] == 512
    assert manifest["limits"]["input_tokens"] == 25_000_000
    assert manifest["limits"]["output_tokens"] == 1_000_000
    assert {code: item["version"] for code, item in manifest["skills"].items()} == {
        "business-investment-assessment": "2.0.0",
        "evidence-grounding-audit": "2.2.0",
        "product-technical-audit": "1.0.0",
        "user-validation-designer": "1.1.0",
    }


def _task(agent: str, required: bool = True) -> dict[str, object]:
    return {
        "task_key": agent,
        "target_agent": agent,
        "input_refs": ["requirement-brief:current"],
        "analysis_dimensions": [agent],
        "region_scope": ["Hong Kong"],
        "as_of": date.today().isoformat(),
        "tool_policy": ["launchscope-context.get.v1"],
        "success_conditions": ["produce traceable findings and a SHA-bound report"],
        "required": required,
        "dependencies": [],
        "budget_suggestion": 2,
        "deadline_seconds": 600,
    }


def _full_plan() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "plan_id": str(uuid4()),
        "run_id": str(uuid4()),
        "brief_id": str(uuid4()),
        "plan_version": 1,
        "supersedes_plan_id": None,
        "evaluation_mode": "FULL_POTENTIAL",
        "score_profile_ref": "score-profile:full-potential@1.0",
        "tasks": [_task("user-evidence"), _task("product-engineering"), _task("business-investment")],
        "trimmed_domains": [],
        "budget_suggestion": 6,
        "deadline_suggestion_seconds": 600,
        "completion_policy": "REQUIRE_ALL",
        "replan_reason": None,
    }


def test_full_plan_accepts_three_independent_domain_tasks() -> None:
    ManagerPlanValidator().validate(_full_plan())


def test_frozen_legacy_plan_validation_ignores_later_report_flag_enable(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_REPORT_V2_ENABLED", "true")

    ManagerPlanValidator().validate(_full_plan(), report_v2=False)


def test_report_v22_manager_plan_v2_keeps_its_published_planning_profile() -> None:
    plan = _full_plan()
    plan["schema_version"] = "2.0"
    for item in plan["tasks"]:
        item["tool_policy"] = ["launchscope-context.get.v2"]
        item["material_scope"] = [{
            "scope_id": str(uuid4()),
            "material_id": str(uuid4()),
            "unit_refs": [f"material-unit:{uuid4()}@{'a' * 64}"],
            "reason": "bounded source for this planning task",
            "required": True,
        }]

    ManagerPlanValidator().validate(plan, report_v2=True)


def test_v4_manifest_freezes_configured_model_pricing(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_PROVIDER_COST_MODE", "TOKEN_ONLY")
    monkeypatch.setenv("LAUNCHSCOPE_MODEL_INPUT_USD_PER_MILLION", "2")
    monkeypatch.setenv("LAUNCHSCOPE_MODEL_OUTPUT_USD_PER_MILLION", "6")

    manifest = ManagerPlanningApplication._run_manifest("FULL_POTENTIAL")

    assert manifest["model_pricing"] == {
        "cost_mode": "TOKEN_ONLY",
        "input_usd_per_million_tokens": "2",
        "output_usd_per_million_tokens": "6",
        "required_before_submission": True,
    }


def test_v4_manifest_freezes_provider_usage_limits() -> None:
    manifest = ManagerPlanningApplication._run_manifest("FULL_POTENTIAL")

    assert manifest["limits"] == {
        "model_calls": 256,
        "input_tokens": 5_000_000,
        "output_tokens": 500_000,
        "search_queries": 8,
        "browser_calls_per_task": 2,
        "browser_seconds": 600,
        "task_timeout_seconds": 3600,
        "targeted_remediation_rounds": 1,
        "reaudit_rounds": 1,
    }


def test_v4_manifest_freezes_student_report_language() -> None:
    chinese = ManagerPlanningApplication._run_manifest("FULL_POTENTIAL", "zh-CN")
    english = ManagerPlanningApplication._run_manifest("FULL_POTENTIAL", "en")

    assert chinese["report_preferences"] == {
        "locale": "zh-CN",
        "audience": "student",
        "tone": "clear_concise_practical",
    }
    assert english["report_preferences"]["locale"] == "en"


def test_v4_manifest_freezes_authorized_research_target(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_AUTHORIZED_CASE_URL", "https://creatrades.com")
    monkeypatch.setenv("LAUNCHSCOPE_BROWSER_ALLOWED_DOMAINS", "creatrades.com")

    manifest = ManagerPlanningApplication._run_manifest("FULL_POTENTIAL")

    assert manifest["research_targets"] == {"authorized_urls": ["https://creatrades.com"]}


def test_specialty_plan_allows_only_profile_controlled_trimming() -> None:
    plan = _full_plan()
    plan.update(
        evaluation_mode="USER_VALIDATION",
        score_profile_ref="score-profile:user-validation@1.0",
        tasks=[_task("user-evidence")],
        trimmed_domains=[
            {"agent_code": "product-engineering", "reason": "outside user-validation scope"},
            {"agent_code": "business-investment", "reason": "outside user-validation scope"},
        ],
        budget_suggestion=2,
        completion_policy="REQUIRE_ALL",
    )
    ManagerPlanValidator().validate(plan)


@pytest.mark.parametrize("mutation", ["missing_agent", "forbidden_tool", "budget", "dependency"])
def test_illegal_manager_plan_cannot_pass_control_plane_validation(mutation: str) -> None:
    plan = deepcopy(_full_plan())
    if mutation == "missing_agent":
        plan["tasks"] = plan["tasks"][:-1]
        plan["trimmed_domains"] = [{"agent_code": "business-investment", "reason": "manager omitted it"}]
    elif mutation == "forbidden_tool":
        plan["tasks"][0]["tool_policy"] = ["run.write"]
    elif mutation == "budget":
        plan["budget_suggestion"] = 21
    else:
        plan["tasks"][0]["dependencies"] = ["product-engineering"]
    with pytest.raises(ManagerPlanValidationError):
        ManagerPlanValidator(budget_cap=Decimal("20")).validate(plan)


def test_cent_rounding_is_normalized_without_exceeding_the_budget_cap() -> None:
    plan = deepcopy(_full_plan())
    plan["budget_suggestion"] = 20
    for item in plan["tasks"]:
        item["budget_suggestion"] = 6.67

    ManagerPlanValidator(budget_cap=Decimal("20")).validate(plan)

    assert _normalized_task_budget_suggestions(plan) == (
        Decimal("6.67"),
        Decimal("6.67"),
        Decimal("6.66"),
    )
