from __future__ import annotations

from uuid import uuid4

import pytest

from launchscope_api.modules.supervisor.specialist_report_v3 import SpecialistReportV3Adapter, SpecialistReportV3Error


@pytest.mark.parametrize(
    ("agent_code", "kind", "required_key"),
    [
        ("user-evidence", "USER_EVIDENCE", "retention_and_payment"),
        ("product-engineering", "PRODUCT_ENGINEERING", "dependencies_and_security"),
        ("business-investment", "BUSINESS_INVESTMENT", "competition_and_market"),
        ("evidence-auditor", "EVIDENCE_AUDIT", "source_independence"),
    ],
)
def test_adapter_seals_role_specific_v3_documents(
    agent_code: str,
    kind: str,
    required_key: str,
) -> None:
    claim_id = f"claim-{agent_code}"
    source_locator_id = uuid4()
    evidence_id = uuid4()
    source = {
        "source_locator_id": str(source_locator_id),
        "evidence_id": str(evidence_id),
        "source_kind": "PUBLIC_URL",
        "canonical_url": "https://example.test/source",
        "title": "可核验来源",
        "publisher": "示例机构",
        "published_at": None,
        "fetched_at": "2026-08-15T00:00:00Z",
        "locator": {"section": "结论"},
        "region": "HK",
        "independence_group": "example",
        "content_sha256": "a" * 64,
    }
    document = {
        "schema_version": "2.0",
        "report_id": str(uuid4()),
        "run_id": str(uuid4()),
        "project_id": str(uuid4()),
        "product_version_id": str(uuid4()),
        "product_title": "校园模拟面试工具",
        "agent_code": agent_code,
        "source_sha256": "b" * 64,
        "executive_summary": [claim_id],
        "metrics": [{"key": "coverage", "label": "覆盖", "value": 1, "claim_ids": [claim_id]}],
        "claims": [
            {
                "claim_id": claim_id,
                "section": "ASSESSMENT",
                "text": "当前证据只支持有条件继续验证。",
                "status": "PENDING_VALIDATION" if agent_code == "evidence-auditor" else "VERIFIED",
                "decision_relevance": "CRITICAL",
                "citation_ids": ["citation-core"],
                "score_bearing": agent_code != "evidence-auditor",
            }
        ],
        "domain_payload": {"stage": "DEMO", "core_flows": ["完成一次核心任务"]},
        "risks": [] if agent_code == "evidence-auditor" else [claim_id],
        "actions": [
            {
                "action_id": "action-core",
                "title": "完成复验",
                "owner": "product-engineering" if agent_code == "evidence-auditor" else "项目负责人",
                "deadline_days": 14,
                "success_criteria": ["核心流程复验通过"],
                "failure_triggers": ["没有新增证据"],
                "required_evidence": ["行为记录"],
                "related_claim_ids": [claim_id],
            }
        ],
        "citations": [
            {
                "citation_id": "citation-core",
                "claim_id": claim_id,
                "evidence_id": str(evidence_id),
                "source_locator_id": str(source_locator_id),
                "support_role": "SUPPORT",
                "audit_status": "VERIFIED",
                "label": 1,
            }
        ],
        "source_directory": [
            source,
            {**source, "source_locator_id": str(uuid4()), "canonical_url": "https://example.test/uncited"},
        ],
        "audit_summary": {"verified": 1, "insufficient": 0, "needs_more": 0, "conflicted": 0},
        "raw_audit_refs": [],
    }

    result = SpecialistReportV3Adapter().adapt(document, locale="zh-CN")

    assert result["schema_version"] == "3.0"
    assert result["locale"] == "zh-CN"
    assert result["domain_payload"]["kind"] == kind
    assert result["domain_payload"][required_key]
    assert len(result["source_directory"]) == 1
    if agent_code == "evidence-auditor":
        assert result["actions"][0]["owner"] == "产品经理"
        assert result["risks"] == [claim_id]


def test_adapter_rejects_english_generated_prose_under_a_frozen_chinese_locale() -> None:
    claim_id = "claim-user-evidence"
    document = {
        "schema_version": "2.0",
        "report_id": str(uuid4()),
        "run_id": str(uuid4()),
        "project_id": str(uuid4()),
        "product_version_id": str(uuid4()),
        "product_title": "校园模拟面试工具",
        "agent_code": "user-evidence",
        "source_sha256": "b" * 64,
        "executive_summary": [claim_id],
        "metrics": [],
        "claims": [{
            "claim_id": claim_id,
            "section": "ASSESSMENT",
            "text": "This conclusion is written in the wrong report language.",
            "status": "PENDING_VALIDATION",
            "decision_relevance": "IMPORTANT",
            "citation_ids": [],
            "score_bearing": False,
        }],
        "domain_payload": {},
        "risks": [claim_id],
        "actions": [{
            "action_id": "action-core",
            "title": "完成复验",
            "owner": "项目负责人",
            "deadline_days": 14,
            "success_criteria": ["获得可复核证据"],
            "failure_triggers": ["没有新增证据"],
            "required_evidence": ["行为记录"],
            "related_claim_ids": [claim_id],
        }],
        "citations": [],
        "source_directory": [],
        "audit_summary": {"verified": 0, "insufficient": 0, "needs_more": 1, "conflicted": 0},
        "raw_audit_refs": [],
    }

    with pytest.raises(SpecialistReportV3Error, match="frozen locale"):
        SpecialistReportV3Adapter().adapt(document, locale="zh-CN")


def test_adapter_fail_closes_claims_that_are_stronger_than_their_citations() -> None:
    claim_id = "claim-evidence-gap"
    document = {
        "schema_version": "2.0",
        "report_id": str(uuid4()),
        "run_id": str(uuid4()),
        "project_id": str(uuid4()),
        "product_version_id": str(uuid4()),
        "product_title": "创意电商工具",
        "agent_code": "evidence-auditor",
        "source_sha256": "b" * 64,
        "executive_summary": [claim_id],
        "metrics": [],
        "claims": [{
            "claim_id": claim_id,
            "section": "ASSESSMENT",
            "text": "平台行为证据仍存在缺口。",
            "status": "VERIFIED",
            "decision_relevance": "CRITICAL",
            "citation_ids": [],
            "score_bearing": True,
        }],
        "domain_payload": {},
        "risks": ["claim-existing-risk"],
        "actions": [{
            "action_id": "action-core",
            "title": "补齐行为证据",
            "owner": "evidence-auditor",
            "deadline_days": 14,
            "success_criteria": ["获得可复核证据"],
            "failure_triggers": ["没有新增证据"],
            "required_evidence": ["行为记录"],
            "related_claim_ids": [claim_id],
        }],
        "citations": [],
        "source_directory": [],
        "audit_summary": {"verified": 0, "insufficient": 0, "needs_more": 1, "conflicted": 0},
        "raw_audit_refs": [],
    }

    result = SpecialistReportV3Adapter().adapt(document, locale="zh-CN")

    assert result["claims"][0]["status"] == "PENDING_VALIDATION"
    assert result["claims"][0]["score_bearing"] is False
    assert result["risks"] == ["claim-existing-risk", claim_id]
