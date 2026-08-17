from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"


def _document(relative: str) -> dict[str, object]:
    return json.loads((CONTRACTS / relative).read_text(encoding="utf-8"))


def _validator(relative: str) -> Draft202012Validator:
    schema = _document(relative)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.fixture
def valid_supervisor_report() -> dict[str, object]:
    run_id = str(uuid4())
    report_id = str(uuid4())
    evidence_id = str(uuid4())
    locator_id = str(uuid4())
    return {
        "schema_version": "2.0",
        "report_id": report_id,
        "run_id": run_id,
        "project_id": str(uuid4()),
        "product_version_id": str(uuid4()),
        "product_title": "示例产品",
        "source_sha256": "a" * 64,
        "top_card": {
            "potential_index": 63,
            "stage": "MVP",
            "confidence_band": "MEDIUM",
            "evidence_coverage": 0.55,
            "recommendation": "VALIDATE_FURTHER",
        },
        "summary_claim_id": "claim-summary",
        "claims": [
            {
                "claim_id": "claim-summary",
                "section": "CONCLUSION",
                "text": "已有真实付费，但续费仍需验证。",
                "status": "VERIFIED",
                "decision_relevance": "CRITICAL",
                "citation_ids": ["citation-1"],
                "score_bearing": True,
            }
        ],
        "highlights": ["claim-summary"],
        "critical_issues": [],
        "role_summaries": {
            "user": ["claim-summary"],
            "product": [],
            "investment": [],
        },
        "cross_domain_claims": ["claim-summary"],
        "actions": [
            {
                "action_id": "action-1",
                "title": "验证续费",
                "owner": "项目负责人",
                "deadline_days": 14,
                "success_criteria": ["10 名目标用户中至少 3 名续费"],
                "failure_triggers": ["0 名续费"],
                "required_evidence": ["订单记录"],
                "related_claim_ids": ["claim-summary"],
            }
        ],
        "confidence_breakdown": {
            "profile_ref": "confidence:full-potential@2.0",
            "audited_evidence_quality": 0.6,
            "evidence_coverage": 0.55,
            "independent_source_support": 0.5,
            "freshness": 0.8,
            "cross_domain_agreement": 0.7,
            "unresolved_conflict_penalty": 0.0,
            "score": 0.61,
            "band": "MEDIUM",
        },
        "agent_report_cards": [
            {
                "agent_code": agent_code,
                "report_id": str(uuid4()),
                "title": title,
                "summary_claim_ids": ["claim-summary"],
                "source_sha256": "b" * 64,
            }
            for agent_code, title in (
                ("user-evidence", "用户报告"),
                ("product-engineering", "产品经理报告"),
                ("business-investment", "投资人报告"),
                ("evidence-auditor", "证据校准报告"),
            )
        ],
        "citations": [
            {
                "citation_id": "citation-1",
                "claim_id": "claim-summary",
                "evidence_id": evidence_id,
                "source_locator_id": locator_id,
                "support_role": "SUPPORT",
                "audit_status": "VERIFIED",
                "label": 1,
            }
        ],
        "source_directory": [
            {
                "source_locator_id": locator_id,
                "evidence_id": evidence_id,
                "source_kind": "PUBLIC_URL",
                "canonical_url": "https://example.com/report",
                "title": "Market report",
                "publisher": "Example Institute",
                "published_at": "2026-01-01T00:00:00Z",
                "fetched_at": "2026-08-13T00:00:00Z",
                "locator": {"page": 12, "section": "Retention"},
                "region": "HK",
                "independence_group": "example-institute:market-report-2026",
                "content_sha256": "c" * 64,
            }
        ],
        "audit_detail_ref": "audit:summary",
    }


def test_new_report_contracts_are_valid_draft_2020_12_schemas() -> None:
    for relative in (
        "reports/citation-source.v1.json",
        "reports/report-comparison.v1.json",
        "reports/specialist-report.v2.json",
        "reports/supervisor-report.v2.json",
        "manager/manager-synthesis.v2.json",
        "audit/audit-result.v4.json",
        "score/score-profile.v2.json",
        "manager/run-manifest.v6.json",
        "handoffs/agent-handoff.v4.json",
    ):
        _validator(relative)


def test_recovery_handoff_v4_expands_epoch_without_mutating_v3() -> None:
    v3 = _document("handoffs/agent-handoff.v3.json")
    v4 = _document("handoffs/agent-handoff.v4.json")

    assert v3["properties"]["dispatch_epoch"]["maximum"] == 1
    assert v4["properties"]["schema_version"]["const"] == "4.0"
    assert v4["properties"]["dispatch_epoch"]["maximum"] == 2_147_483_647


def test_critical_verified_claim_requires_citation(valid_supervisor_report: dict[str, object]) -> None:
    claim = valid_supervisor_report["claims"][0]
    claim["citation_ids"] = []
    errors = list(_validator("reports/supervisor-report.v2.json").iter_errors(valid_supervisor_report))
    assert any(list(error.absolute_path) == ["claims", 0] for error in errors)


def test_pending_claim_may_have_no_citation_but_is_not_score_bearing(
    valid_supervisor_report: dict[str, object],
) -> None:
    claim = valid_supervisor_report["claims"][0]
    claim.update(status="PENDING_VALIDATION", citation_ids=[], score_bearing=False)
    assert list(_validator("reports/supervisor-report.v2.json").iter_errors(valid_supervisor_report)) == []


def test_pending_claim_cannot_be_score_bearing(valid_supervisor_report: dict[str, object]) -> None:
    claim = valid_supervisor_report["claims"][0]
    claim.update(status="PENDING_VALIDATION", citation_ids=[], score_bearing=True)
    with pytest.raises(ValidationError):
        _validator("reports/supervisor-report.v2.json").validate(valid_supervisor_report)


def test_recommendation_is_four_actions_and_never_probability(valid_supervisor_report: dict[str, object]) -> None:
    schema_text = (CONTRACTS / "reports" / "supervisor-report.v2.json").read_text(encoding="utf-8")
    assert "ABANDON" not in schema_text
    assert "probability" not in schema_text.lower()
    recommendation = _document("reports/supervisor-report.v2.json")["$defs"]["recommendation"]
    assert recommendation["enum"] == ["PROCEED", "VALIDATE_FURTHER", "ADJUST", "PAUSE"]


def test_comparison_delta_is_only_valid_for_comparable() -> None:
    validator = _validator("reports/report-comparison.v1.json")
    base = {
        "schema_version": "1.0",
        "status": "STANDARD_CHANGED",
        "prior_run_id": str(uuid4()),
        "candidate_run_id": str(uuid4()),
        "prior_input_snapshot_sha256": "a" * 64,
        "candidate_input_snapshot_sha256": "b" * 64,
        "prior_score_profile_ref": "score:full-potential@1.0",
        "candidate_score_profile_ref": "score:full-potential@2.0",
        "prior_report_profile_ref": "report:supervisor@1.0",
        "candidate_report_profile_ref": "report:supervisor@2.0",
        "resolved_issues": [],
        "unchanged_issues": [],
        "new_risks": [],
        "evidence_upgrades": [],
        "evidence_downgrades": [],
        "change_reason_claim_ids": [],
    }
    validator.validate(base)
    base["index_delta"] = 5
    with pytest.raises(ValidationError):
        validator.validate(base)


def test_report_contract_has_one_body_not_summary_and_full_bodies() -> None:
    for relative in ("reports/specialist-report.v2.json", "reports/supervisor-report.v2.json"):
        text = (CONTRACTS / relative).read_text(encoding="utf-8")
        assert "summary_body" not in text
        assert "full_body" not in text


def test_v6_identities_are_exactly_physical_one_plus_four() -> None:
    root = CONTRACTS / "manager" / "agents"
    identities = {
        path.stem.split(".")[0]: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in root.glob("*.v6.yaml")
    }
    assert set(identities) == {
        "evaluation-manager",
        "user-evidence",
        "product-engineering",
        "business-investment",
        "evidence-auditor",
    }
    assert identities["evaluation-manager"]["outputs"] == ["manager_plan_v2", "manager_synthesis_v2"]
    assert "geo-policy-trend" not in identities


def test_run_manifest_v6_requires_report_profile_and_five_workers() -> None:
    schema = _document("manager/run-manifest.v6.json")
    required = schema["required"]
    assert "report_profile" in required
    topology = schema["properties"]["physical_topology"]["properties"]
    assert topology["worker_count"]["const"] == 5
    assert topology["workers"]["items"]["enum"] == [
        "user-evidence",
        "product-engineering",
        "business-investment",
        "evidence-auditor",
    ]


def test_full_potential_v2_preserves_index_weights_and_adds_deterministic_metrics() -> None:
    profile = _document("score/profiles/full-potential.v2.json")
    _validator("score/score-profile.v2.json").validate(profile)
    assert profile["weights"] == {
        "user_value": 0.30,
        "product_capability": 0.30,
        "investment_potential": 0.30,
        "evidence_quality": 0.10,
    }
    assert sum(profile["confidence_profile"]["component_weights"].values()) == pytest.approx(1.0)
    assert set(profile["required_dimensions"]) == {
        "user_value",
        "product_capability",
        "investment_potential",
        "evidence_quality",
    }
