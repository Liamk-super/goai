from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"


def _load(relative: str) -> dict[str, object]:
    return json.loads((CONTRACTS / relative).read_text(encoding="utf-8"))


def _errors(relative: str, document: dict[str, object]):
    return list(
        Draft202012Validator(
            _load(relative),
            format_checker=FormatChecker(),
        ).iter_errors(document)
    )


def _identity(agent_code: str) -> dict[str, object]:
    return {
        "schema_version": "3.0",
        "locale": "zh-CN",
        "report_id": "00000000-0000-4000-8000-000000000001",
        "run_id": "00000000-0000-4000-8000-000000000002",
        "project_id": "00000000-0000-4000-8000-000000000003",
        "product_version_id": "00000000-0000-4000-8000-000000000004",
        "product_title": "面向大学生的 AI 模拟面试工具",
        "agent_code": agent_code,
        "source_sha256": "a" * 64,
    }


def _claim(claim_id: str = "claim-core") -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "section": "ASSESSMENT",
        "text": "当前证据只支持继续验证。",
        "status": "DOWNGRADED",
        "decision_relevance": "CRITICAL",
        "citation_ids": ["citation-core"],
        "score_bearing": True,
    }


def _citation(claim_id: str = "claim-core") -> dict[str, object]:
    return {
        "citation_id": "citation-core",
        "claim_id": claim_id,
        "evidence_id": "00000000-0000-4000-8000-000000000005",
        "source_locator_id": None,
        "support_role": "SUPPORT",
        "audit_status": "DOWNGRADED",
        "label": 1,
    }


def _action(claim_id: str = "claim-core") -> dict[str, object]:
    return {
        "action_id": "action-core",
        "title": "完成下一轮验证",
        "owner": "项目负责人",
        "deadline_days": 14,
        "success_criteria": ["获得可复核的目标用户行为证据"],
        "failure_triggers": ["没有新增有效证据"],
        "required_evidence": ["用户任务记录"],
        "related_claim_ids": [claim_id],
    }


def _specialist(agent_code: str, domain_payload: dict[str, object]) -> dict[str, object]:
    claim = _claim()
    return {
        **_identity(agent_code),
        "executive_summary": [claim["claim_id"]],
        "metrics": [],
        "claims": [claim],
        "domain_payload": domain_payload,
        "risks": [claim["claim_id"]],
        "actions": [_action()],
        "citations": [_citation()],
        "source_directory": [],
        "audit_summary": {"verified": 0, "insufficient": 1, "needs_more": 0, "conflicted": 0},
        "raw_audit_refs": [],
    }


def test_adr_0024_accepts_additive_report_quality_contract_generation() -> None:
    adr = (ROOT / "docs" / "adr" / "0024-report-v3-quality-and-frontend-admission.md").read_text(encoding="utf-8")
    assert "Status: Accepted" in adr
    assert "SupervisorReportDocumentV3" in adr
    assert "SpecialistReportDocumentV3" in adr
    assert "v1/v2" in adr


def test_published_report_v2_contracts_remain_byte_identical() -> None:
    expected = {
        "reports/supervisor-report.v2.json": "a9e05912dee3982d16000d8116ad8f442f3e4fb40c5b06cd4bf04ba528839f23",
        "reports/specialist-report.v2.json": "c4962bab2a7c99a1a94486c698c1be554b66f1d199b58183538e611b611e2c39",
    }
    for relative, digest in expected.items():
        assert hashlib.sha256((CONTRACTS / relative).read_bytes()).hexdigest() == digest


def test_supervisor_v3_requires_locale_dimensions_and_driver_claims() -> None:
    schema = _load("reports/supervisor-report.v3.json")
    required = set(schema["required"])
    assert {"locale", "dimension_scores", "evidence_coverage_profile"}.issubset(required)
    dimensions = schema["properties"]["dimension_scores"]
    assert set(dimensions["required"]) == {
        "user_value",
        "product_capability",
        "investment_potential",
        "evidence_quality",
    }


def test_manager_synthesis_v3_freezes_locale_without_authoritative_scores() -> None:
    schema = _load("manager/manager-synthesis.v3.json")
    assert {"locale", "claims", "actions"}.issubset(set(schema["required"]))
    properties = schema["properties"]
    assert properties["locale"]["enum"] == ["zh-CN", "en"]
    for forbidden in ("potential_index", "dimension_scores", "recommendation", "confidence"):
        assert forbidden not in properties


def test_run_manifest_v7_binds_v3_canonical_reports_without_relabeling_v6_agent_inputs() -> None:
    schema = _load("manager/run-manifest.v7.json")
    properties = schema["properties"]
    assert properties["architecture_generation"]["const"] == "supervisor-1p4-report-v3"
    assert properties["feature_flag"]["const"] == "LAUNCHSCOPE_REPORT_V3_ENABLED"
    assert properties["agent_contract_generation"]["const"] == "v6"
    required_contracts = set(properties["contracts"]["required"])
    assert {"manager_synthesis_input", "specialist_report_input", "specialist_report", "supervisor_report"}.issubset(
        required_contracts
    )


def test_specialist_v3_rejects_sparse_arbitrary_domain_payloads() -> None:
    for agent_code in (
        "user-evidence",
        "product-engineering",
        "business-investment",
        "evidence-auditor",
    ):
        assert _errors("reports/specialist-report.v3.json", _specialist(agent_code, {}))


def test_specialist_v3_accepts_role_specific_professional_structures() -> None:
    payloads = {
        "user-evidence": {
            "kind": "USER_EVIDENCE",
            "target_segments": ["准备校招的大学生"],
            "jobs_and_scenarios": ["面试前反复练习"],
            "behavioral_evidence": ["完成一次模拟面试"],
            "retention_and_payment": ["续用与付费仍待验证"],
            "validation_plan": ["观察两周内再次使用"],
        },
        "product-engineering": {
            "kind": "PRODUCT_ENGINEERING",
            "stage_gate": "DEMO",
            "core_flows": ["创建面试并获得反馈"],
            "delivery_and_reliability": ["稳定性仍待压测"],
            "dependencies_and_security": ["模型依赖需持续验证"],
            "retest_gates": ["核心流程成功率达到目标"],
        },
        "business-investment": {
            "kind": "BUSINESS_INVESTMENT",
            "business_model": ["订阅模式假设"],
            "unit_economics": ["获客成本仍待验证"],
            "competition_and_market": ["需补充有来源的竞品证据"],
            "investment_gates": ["先验证续费再扩大投入"],
            "compliance_scope": ["按目标地区复核"],
        },
        "evidence-auditor": {
            "kind": "EVIDENCE_AUDIT",
            "coverage_by_dimension": ["用户价值证据有限"],
            "source_independence": ["当前独立来源不足"],
            "conflicts": ["团队陈述与行为数据尚未互证"],
            "calibration_decisions": ["关键 Claim 降级"],
            "evidence_gaps": ["缺少续费记录"],
        },
    }
    for agent_code, payload in payloads.items():
        assert not _errors("reports/specialist-report.v3.json", _specialist(agent_code, payload))
