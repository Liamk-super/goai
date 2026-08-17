from __future__ import annotations

from uuid import uuid4

import pytest

from launchscope_api.modules.evaluation.intake_application import IntakeValidationError
from launchscope_api.modules.supervisor.intake_application import RequirementBriefNormalizer, supervisor_1p4_enabled


def _clear_proposal() -> dict[str, object]:
    return {
        "normalized_goal": "判断是否值得继续投入",
        "evaluation_mode": "FULL_POTENTIAL",
        "requested_deliverables": ["完整评审报告"],
        "constraints": ["不调用付费服务"],
        "success_criteria": ["给出可追溯建议"],
        "explicit_facts": {
            "target_user": "香港大学生创业团队",
            "region": "香港",
            "validation_goal": "判断是否值得继续投入",
        },
        "assumptions": [],
        "unknowns": ["payer"],
        "confidence_overall": 0.94,
        "confidence_fields": {"target_user": 0.95, "region": 0.96, "validation_goal": 0.94},
        "change_classification": "INITIAL",
        "scope_changed": False,
        "cost_changed": False,
        "permission_changed": False,
    }


def _normalize(proposal: dict[str, object]):
    raw = "请评估面向香港大学生创业团队的产品，范围是香港，判断是否值得继续投入；不调用付费服务。"
    return RequirementBriefNormalizer().normalize(
        tenant_id=uuid4(),
        product_version_id=uuid4(),
        raw_content=raw,
        raw_object_key="tenant/test/raw.txt",
        raw_sha256="a" * 64,
        revision=1,
        model_output=proposal,
    )


def test_clear_grounded_requirement_proceeds_without_confirmation() -> None:
    result = _normalize(_clear_proposal())
    assert result.document["confirmation_required"] is False
    assert result.document["confirmation_reasons"] == []
    assert result.questions == ()
    assert result.document["unknowns"] == ["payer"]


def test_critical_ambiguity_asks_only_the_missing_question() -> None:
    proposal = _clear_proposal()
    proposal["explicit_facts"] = {"region": "香港", "validation_goal": "判断是否值得继续投入"}
    proposal["confidence_overall"] = 0.7
    result = _normalize(proposal)
    assert result.document["confirmation_required"] is True
    assert result.document["confirmation_reasons"] == ["CRITICAL_AMBIGUITY"]
    assert result.questions == ("Who is the primary target user for this review?",)


def test_model_assumption_is_exposed_and_requires_confirmation() -> None:
    proposal = _clear_proposal()
    proposal["assumptions"] = [{"field": "payer", "value": "学校采购部门", "material": True}]
    result = _normalize(proposal)
    assert result.document["explicit_facts"].get("payer") is None
    assert result.document["confirmation_reasons"] == ["MODEL_ASSUMPTION"]
    assert result.questions == ("Please confirm or correct the assumption for payer: 学校采购部门",)


def test_model_cannot_add_an_ungrounded_explicit_fact() -> None:
    proposal = _clear_proposal()
    proposal["explicit_facts"] = {**proposal["explicit_facts"], "payer": "学校采购部门"}
    with pytest.raises(IntakeValidationError, match="exact user-expressed span"):
        _normalize(proposal)


def test_explicit_chinese_validation_goal_is_recovered_as_an_exact_span() -> None:
    proposal = _clear_proposal()
    proposal["normalized_goal"] = "评审 CreaTrades"
    proposal["explicit_facts"] = {
        "target_user": "跨境电商卖家",
        "region": "美国和欧洲",
    }
    proposal["unknowns"] = ["validation_goal"]
    proposal["confidence_fields"] = {"target_user": 0.95, "region": 0.95}
    raw = (
        "评审 CreaTrades，目标用户是跨境电商卖家，地区是美国和欧洲。"
        "本轮评审要判断未来 12 个月内产品需求、技术可交付性和商业投入价值。"
    )

    result = RequirementBriefNormalizer().normalize(
        tenant_id=uuid4(),
        product_version_id=uuid4(),
        raw_content=raw,
        raw_object_key="tenant/test/raw.txt",
        raw_sha256="a" * 64,
        revision=1,
        model_output=proposal,
    )

    assert result.document["explicit_facts"]["validation_goal"] == (
        "本轮评审要判断未来 12 个月内产品需求、技术可交付性和商业投入价值。"
    )
    assert "validation_goal" not in result.document["unknowns"]
    assert result.document["confirmation_required"] is False


def test_supervisor_generation_feature_flag_defaults_closed(monkeypatch) -> None:
    monkeypatch.delenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", raising=False)
    assert supervisor_1p4_enabled() is False
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "true")
    assert supervisor_1p4_enabled() is True
