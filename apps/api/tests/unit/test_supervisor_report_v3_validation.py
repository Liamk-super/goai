from __future__ import annotations

import hashlib
import json
from typing import Any, cast
from uuid import uuid4

import pytest

from launchscope_api.modules.experience.api import _load_report_v3
from launchscope_api.modules.project_dossier.material_ingestion import ObjectMetadata
from launchscope_api.modules.supervisor.report_v3 import SupervisorReportV3Builder, SupervisorReportV3Error

SHA = "b" * 64
DIMENSIONS = ("user_value", "product_capability", "investment_potential", "evidence_quality")


class _Objects:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def head(self, _key: str) -> ObjectMetadata:
        return ObjectMetadata(
            sha256=hashlib.sha256(self.body).hexdigest(),
            size_bytes=len(self.body),
            mime_type="application/json",
            etag="v3",
            metadata={},
        )

    def get_private(self, _key: str, *, max_bytes: int) -> bytes:
        assert max_bytes == 2_000_000
        return self.body


def _claim(claim_id: str, *, status: str = "VERIFIED", section: str = "CONCLUSION") -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "section": section,
        "text": f"{claim_id} 的可核验结论。",
        "status": status,
        "decision_relevance": "CRITICAL" if section in {"CONCLUSION", "CRITICAL_ISSUE"} else "IMPORTANT",
        "citation_ids": [f"citation-{claim_id.removeprefix('claim-')}"],
        "score_bearing": status in {"VERIFIED", "DOWNGRADED"},
    }


def _inputs() -> dict[str, object]:
    run_id, project_id, version_id, decision_id, report_id = (uuid4() for _ in range(5))
    summary = _claim("claim-summary")
    issue = _claim("claim-core-risk", status="DOWNGRADED", section="CRITICAL_ISSUE")
    synthesis = {
        "schema_version": "3.0",
        "locale": "zh-CN",
        "synthesis_id": str(uuid4()),
        "run_id": str(run_id),
        "deterministic_decision_ref": str(decision_id),
        "summary_claim_id": "claim-summary",
        "claims": [summary, issue],
        "actions": [
            {
                "action_id": "action-validate",
                "title": "验证真实复用",
                "owner": "项目负责人",
                "deadline_days": 14,
                "success_criteria": ["目标用户完成两次核心任务"],
                "failure_triggers": ["没有用户再次使用"],
                "required_evidence": ["行为记录"],
                "related_claim_ids": ["claim-core-risk"],
            }
        ],
        "decision_conflict": False,
    }
    driver_claims = []
    for index, dimension in enumerate(DIMENSIONS):
        status = "PENDING_VALIDATION" if dimension == "investment_potential" else "DOWNGRADED"
        claim = _claim(f"claim-driver-{dimension}", status=status, section="INFORMATION_GAP")
        if status == "PENDING_VALIDATION":
            claim["citation_ids"] = []
        driver_claims.append(
            {
                "dimension": dimension,
                "polarity": "PENDING" if status == "PENDING_VALIDATION" else "POSITIVE" if index == 0 else "NEGATIVE",
                "claim": claim,
            }
        )
    claims = [summary, issue, *[item["claim"] for item in driver_claims if item["claim"]["citation_ids"]]]
    evidence_ids = [uuid4() for _ in claims]
    locator_ids = [uuid4() for _ in claims]
    citations = [
        {
            "citation_id": claim["citation_ids"][0],
            "claim_id": claim["claim_id"],
            "evidence_id": str(evidence_id),
            "source_locator_id": str(locator_id),
            "support_role": "SUPPORT",
            "audit_status": "VERIFIED" if claim["status"] == "VERIFIED" else "DOWNGRADED",
            "label": index,
        }
        for index, (claim, evidence_id, locator_id) in enumerate(zip(claims, evidence_ids, locator_ids, strict=True), 1)
    ]
    source_directory = [
        {
            "source_locator_id": str(locator_id),
            "evidence_id": str(evidence_id),
            "source_kind": "PUBLIC_URL",
            "canonical_url": f"https://example.test/source/{index}",
            "title": f"来源 {index}",
            "publisher": "示例机构",
            "published_at": None,
            "fetched_at": "2026-08-15T00:00:00Z",
            "locator": {"section": "结论"},
            "region": "HK",
            "independence_group": f"source-{index}",
            "content_sha256": SHA,
        }
        for index, (evidence_id, locator_id) in enumerate(zip(evidence_ids, locator_ids, strict=True), 1)
    ]
    source_directory.append(
        {
            **source_directory[0],
            "source_locator_id": str(uuid4()),
            "canonical_url": "https://example.test/uncited-search-result",
            "title": "未引用的搜索结果",
        }
    )
    confidence = {
        "profile_ref": "score-profile:full-potential@2.0",
        "audited_evidence_quality": 0.65,
        "evidence_coverage": 0.75,
        "independent_source_support": 0.5,
        "freshness": 1.0,
        "cross_domain_agreement": 0.8,
        "unresolved_conflict_penalty": 0.0,
        "score": 0.7,
        "band": "MEDIUM",
    }
    return {
        "report_id": report_id,
        "run": {
            "id": run_id,
            "project_id": project_id,
            "product_version_id": version_id,
            "product_title": "校园模拟面试工具",
            "stage": "DEMO",
            "locale": "zh-CN",
        },
        "decision": {
            "id": decision_id,
            "recommendation": "VALIDATE_FURTHER",
            "dimension_grades": {
                "score": 63,
                "dimension_scores": {
                    "user_value": 72,
                    "product_capability": 64,
                    "investment_potential": None,
                    "evidence_quality": 55,
                },
                "evidence_coverage": 0.75,
                "confidence_breakdown": confidence,
                "comparison": {"status": "FIRST_EVALUATION"},
            },
        },
        "synthesis": synthesis,
        "driver_claims": driver_claims,
        "citations": citations,
        "source_directory": source_directory,
        "agent_report_cards": [
            {
                "agent_code": code,
                "report_id": str(uuid4()),
                "title": title,
                "summary_claim_ids": ["claim-summary"],
                "source_sha256": SHA,
            }
            for code, title in (
                ("user-evidence", "目标用户报告"),
                ("product-engineering", "产品经理报告"),
                ("business-investment", "投资人报告"),
                ("evidence-auditor", "证据校准报告"),
            )
        ],
        "allowed_evidence_ids": set(evidence_ids),
        "source_sha256": SHA,
        "audit_detail_ref": "audit:run:fixture",
    }


def _build(inputs: dict[str, object]) -> dict[str, object]:
    return SupervisorReportV3Builder().build(**inputs)


def test_builds_four_deterministic_dimensions_and_filters_uncited_sources() -> None:
    document = _build(_inputs())

    assert document["schema_version"] == "3.0"
    assert document["locale"] == "zh-CN"
    assert set(document["dimension_scores"]) == set(DIMENSIONS)
    assert document["dimension_scores"]["user_value"]["value"] == 72
    assert document["dimension_scores"]["investment_potential"]["value"] is None
    assert document["dimension_scores"]["investment_potential"]["pending_validation_claim_ids"]
    assert len(document["issue_priorities"]) <= 3
    assert all(source["title"] != "未引用的搜索结果" for source in document["source_directory"])


def test_rejects_locale_mismatch_between_run_and_synthesis() -> None:
    inputs = _inputs()
    inputs["synthesis"]["locale"] = "en"

    with pytest.raises(SupervisorReportV3Error, match="locale"):
        _build(inputs)


def test_rejects_english_generated_prose_under_a_frozen_chinese_locale() -> None:
    inputs = _inputs()
    inputs["synthesis"]["claims"][0]["text"] = "This conclusion is written in the wrong report language."

    with pytest.raises(SupervisorReportV3Error, match="frozen locale"):
        _build(inputs)


def test_rejects_verified_claim_backed_only_by_downgraded_citation() -> None:
    inputs = _inputs()
    inputs["citations"][0]["audit_status"] = "DOWNGRADED"

    with pytest.raises(SupervisorReportV3Error, match="stronger than"):
        _build(inputs)


def test_rejects_driver_value_that_differs_from_deterministic_decision() -> None:
    inputs = _inputs()
    inputs["driver_claims"][0]["value"] = 99

    with pytest.raises(SupervisorReportV3Error, match="authoritative dimension"):
        _build(inputs)


def test_rejects_driver_claim_without_compatible_citation() -> None:
    inputs = _inputs()
    inputs["citations"] = [
        citation for citation in inputs["citations"] if citation["claim_id"] != "claim-driver-user_value"
    ]

    with pytest.raises(SupervisorReportV3Error, match="Citation"):
        _build(inputs)


def test_v3_read_returns_the_exact_hash_verified_canonical_document() -> None:
    document = _build(_inputs())
    body = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    metadata = {
        "report_id": document["report_id"],
        "run_id": document["run_id"],
        "object_key": "private/report-v3.json",
        "sha256": hashlib.sha256(body).hexdigest(),
        "created_at": "2026-08-15T00:00:00+00:00",
    }

    result = _load_report_v3(metadata, kind="SUPERVISOR", object_store=cast(Any, _Objects(body)))

    assert result["report_schema_version"] == "3.0"
    assert result["document"] == document
    assert result["integrity"]["canonical_sha256"] == metadata["sha256"]
