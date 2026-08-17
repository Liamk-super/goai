from __future__ import annotations

from uuid import uuid4

import pytest

from launchscope_api.modules.supervisor.report_v2 import SupervisorReportV2Builder, SupervisorReportV2Error

SHA = "a" * 64


def _inputs() -> dict[str, object]:
    run_id, project_id, version_id, decision_id, report_id = (uuid4() for _ in range(5))
    evidence_id, locator_id = uuid4(), uuid4()
    claim_id, citation_id = "claim-summary", "citation-summary-1"
    synthesis = {
        "schema_version": "2.0",
        "synthesis_id": str(uuid4()),
        "run_id": str(run_id),
        "deterministic_decision_ref": str(decision_id),
        "summary_claim_id": claim_id,
        "claims": [
            {
                "claim_id": claim_id,
                "section": "CONCLUSION",
                "text": "现有证据支持继续小步验证。",
                "status": "VERIFIED",
                "decision_relevance": "CRITICAL",
                "citation_ids": [citation_id],
                "score_bearing": True,
            }
        ],
        "actions": [
            {
                "action_id": "action-1",
                "title": "补充真实用户验证",
                "owner": "项目负责人",
                "deadline_days": 14,
                "success_criteria": ["获得三条可核验反馈"],
                "failure_triggers": ["无人完成关键任务"],
                "required_evidence": ["访谈记录"],
                "related_claim_ids": [claim_id],
            }
        ],
        "decision_conflict": False,
    }
    confidence = {
        "profile_ref": "score-profile:full-potential@2.0",
        "audited_evidence_quality": 0.8,
        "evidence_coverage": 0.7,
        "independent_source_support": 0.7,
        "freshness": 1.0,
        "cross_domain_agreement": 1.0,
        "unresolved_conflict_penalty": 0.0,
        "score": 0.81,
        "band": "HIGH",
    }
    return {
        "report_id": report_id,
        "run": {
            "id": run_id,
            "project_id": project_id,
            "product_version_id": version_id,
            "product_title": "校园创意交易平台",
            "stage": "早期验证",
        },
        "decision": {
            "id": decision_id,
            "recommendation": "VALIDATE_FURTHER",
            "dimension_grades": {
                "score": 63,
                "evidence_coverage": 0.7,
                "confidence_breakdown": confidence,
                "comparison": {
                    "schema_version": "1.0",
                    "status": "FIRST_EVALUATION",
                    "candidate_run_id": str(run_id),
                    "candidate_input_snapshot_sha256": SHA,
                    "candidate_score_profile_ref": "score-profile:full-potential@2.0",
                    "candidate_report_profile_ref": "supervisor-report@2.0",
                    "resolved_issues": [],
                    "unchanged_issues": [],
                    "new_risks": [],
                    "evidence_upgrades": [],
                    "evidence_downgrades": [],
                    "change_reason_claim_ids": [],
                },
            },
        },
        "synthesis": synthesis,
        "citations": [
            {
                "citation_id": citation_id,
                "claim_id": claim_id,
                "evidence_id": str(evidence_id),
                "source_locator_id": str(locator_id),
                "support_role": "SUPPORT",
                "audit_status": "VERIFIED",
                "label": 1,
            }
        ],
        "source_directory": [
            {
                "source_locator_id": str(locator_id),
                "evidence_id": str(evidence_id),
                "source_kind": "PUBLIC_URL",
                "canonical_url": "https://example.test/report",
                "title": "示例研究",
                "publisher": "示例机构",
                "published_at": None,
                "fetched_at": "2026-08-13T00:00:00Z",
                "locator": {"section": "结论"},
                "region": "HK",
                "independence_group": "example:report",
                "content_sha256": SHA,
            }
        ],
        "agent_report_cards": [
            {
                "agent_code": code,
                "report_id": str(uuid4()),
                "title": title,
                "summary_claim_ids": [claim_id],
                "source_sha256": SHA,
            }
            for code, title in (
                ("user-evidence", "用户报告"),
                ("product-engineering", "产品经理报告"),
                ("business-investment", "投资人报告"),
                ("evidence-auditor", "证据审核报告"),
            )
        ],
        "allowed_evidence_ids": {evidence_id},
        "source_sha256": SHA,
        "audit_detail_ref": "audit:round-1",
    }


def _build(inputs: dict[str, object]) -> dict[str, object]:
    return SupervisorReportV2Builder().build(**inputs)


def test_builds_first_report_without_comparison_projection() -> None:
    document = _build(_inputs())

    assert document["schema_version"] == "2.0"
    assert document["product_title"] == "校园创意交易平台"
    assert document["top_card"]["potential_index"] == 63
    assert "comparison" not in document


def test_rejects_critical_claim_with_unknown_citation() -> None:
    inputs = _inputs()
    inputs["synthesis"]["claims"][0]["citation_ids"] = ["citation-unknown"]

    with pytest.raises(SupervisorReportV2Error, match="unknown Citation"):
        _build(inputs)


def test_rejects_citation_to_evidence_outside_run() -> None:
    inputs = _inputs()
    inputs["allowed_evidence_ids"] = set()

    with pytest.raises(SupervisorReportV2Error, match="outside the Run"):
        _build(inputs)


def test_rejects_verified_claim_backed_only_by_rejected_evidence() -> None:
    inputs = _inputs()
    inputs["citations"][0]["audit_status"] = "REJECTED"

    with pytest.raises(SupervisorReportV2Error, match="verified Claim"):
        _build(inputs)


def test_rejects_action_referring_to_unknown_claim() -> None:
    inputs = _inputs()
    inputs["synthesis"]["actions"][0]["related_claim_ids"] = ["claim-missing"]

    with pytest.raises(SupervisorReportV2Error, match="unknown Claim"):
        _build(inputs)


def test_rejects_manager_supplied_authoritative_fields() -> None:
    inputs = _inputs()
    inputs["synthesis"]["potential_index"] = 99

    with pytest.raises(SupervisorReportV2Error, match="ManagerSynthesisV2"):
        _build(inputs)


def test_rejects_product_identity_mismatch() -> None:
    inputs = _inputs()
    inputs["run"]["product_title"] = ""

    with pytest.raises(SupervisorReportV2Error, match="product title"):
        _build(inputs)
