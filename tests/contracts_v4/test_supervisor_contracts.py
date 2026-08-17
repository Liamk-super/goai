from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from launchscope_orchestrator.manifest_loader import AgentManifestLoader

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"
LEGACY_CONTRACT_SHA256 = "f0fbefc289bfb392deb65f5ee3dc09426b86a0fc5f6eed980064576d16c82da2"
FROZEN_TEST_SHA256 = "233f35c07ccf506e756b9fca82a05badb38f4106b13905b3d94d6cee7fb3cbb4"
NEW_SCHEMAS = (
    "intake/requirement-brief.v1.json",
    "intake/requirement-change.v1.json",
    "manager/manager-plan.v1.json",
    "tasks/agent-task-ticket.v3.json",
    "handoffs/agent-handoff.v3.json",
    "audit/audit-request.v3.json",
    "audit/audit-result.v3.json",
    "score/score-profile.v1.json",
    "manager/manager-synthesis.v1.json",
    "run-manifest/run-manifest.v4.json",
)


def _document(relative: str) -> dict[str, object]:
    return json.loads((CONTRACTS / relative).read_text(encoding="utf-8"))


def _validator(relative: str) -> Draft202012Validator:
    schema = _document(relative)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _aggregate(paths: list[Path]) -> str:
    rows = []
    for path in sorted(paths):
        relative = path.relative_to(ROOT).as_posix()
        rows.append(f"{relative}={hashlib.sha256(path.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def _legacy_contracts() -> list[Path]:
    new_roots = {"intake", "manager", "tasks", "score", "reports"}
    paths = []
    for path in CONTRACTS.rglob("*"):
        if (
            not path.is_file()
            or "tests" in path.parts
            or "__pycache__" in path.parts
            or any(part.endswith(".egg-info") for part in path.parts)
        ):
            continue
        relative = path.relative_to(CONTRACTS)
        if relative.parts[0] in new_roots or ".v4." in path.name:
            continue
        if path.name in {
            "agent-handoff.v3.json",
            "audit-request.v3.json",
            "audit-result.v3.json",
            "run-manifest.v4.json",
            "run-control-events.v1.json",
            "run-conversation.v1.yaml",
            "run-execution-control.v1.yaml",
            "supervisor-chat.v1.yaml",
            "agent-reports.v5.yaml",
            "public-demo-report.v2.yaml",
            "report-experience.v2.yaml",
            "report-export.v1.yaml",
            "run-recovery.v1.yaml",
        }:
            continue
        paths.append(path)
    return paths


def test_legacy_contract_and_frozen_test_sources_are_byte_locked() -> None:
    legacy = _legacy_contracts()
    frozen_tests = sorted((CONTRACTS / "tests").glob("*.py"))
    assert len(legacy) == 46
    assert _aggregate(legacy) == LEGACY_CONTRACT_SHA256
    assert _aggregate(frozen_tests) == FROZEN_TEST_SHA256


def test_run_conversation_openapi_has_exactly_four_user_channels() -> None:
    contract = yaml.safe_load((CONTRACTS / "openapi" / "run-conversation.v1.yaml").read_text(encoding="utf-8"))
    channel = contract["components"]["schemas"]["ConversationChannel"]
    assert contract["openapi"] == "3.1.0"
    assert channel["enum"] == [
        "supervisor",
        "user-evidence",
        "product-engineering",
        "business-investment",
    ]


@pytest.mark.parametrize("relative", NEW_SCHEMAS)
def test_new_json_schemas_are_valid_draft_2020_12(relative: str) -> None:
    _validator(relative)


def test_v4_identity_generation_contains_exactly_physical_one_plus_four() -> None:
    contracts = AgentManifestLoader().load_all("v4")
    assert {item.code for item in contracts} == {
        "evaluation-manager",
        "user-evidence",
        "product-engineering",
        "business-investment",
        "evidence-auditor",
    }
    assert "geo-policy-trend" not in {item.code for item in contracts}
    manager = next(item for item in contracts if item.code == "evaluation-manager")
    assert manager.allowed_tools == ("launchscope-context.get.v1",)
    assert "score.write" in manager.prohibited_actions
    assert {item.code for item in AgentManifestLoader().load_all("v3")} - {item.code for item in contracts} == {
        "geo-policy-trend"
    }


def test_legacy_geo_handoff_and_generation_remain_readable() -> None:
    legacy = _validator("handoffs/agent-handoff.v2.json")
    legacy.validate(
        {
            "schema_version": "2.0",
            "tenant_id": str(uuid4()),
            "run_id": str(uuid4()),
            "task_id": str(uuid4()),
            "dispatch_epoch": 0,
            "agent_code": "geo-policy-trend",
            "status": "SUCCEEDED",
            "dimension": "geo_policy_trend",
            "claims": [{"region": "HK", "as_of": "2026-08-11"}],
            "evidence_refs": [str(uuid4())],
            "risk": "MEDIUM",
            "confidence": 0.7,
            "needs_human_approval": False,
            "failure_class": None,
            "next_action": "retain historical GEO_POLICY_TREND projection",
            "audit_results": [],
            "information_requests": [],
            "skill_result_ref": None,
            "skill_result_sha256": None,
            "validation_mode": None,
        }
    )
    assert "geo-policy-trend" in {item.code for item in AgentManifestLoader().load_all("v3")}


def test_score_profiles_are_versioned_and_weights_sum_to_one() -> None:
    validator = _validator("score/score-profile.v1.json")
    profiles = sorted((CONTRACTS / "score" / "profiles").glob("*.v1.json"))
    assert len(profiles) == 4
    modes = set()
    for path in profiles:
        profile = json.loads(path.read_text(encoding="utf-8"))
        validator.validate(profile)
        assert sum(profile["weights"].values()) == pytest.approx(1.0)
        modes.add(profile["evaluation_mode"])
    assert modes == {"FULL_POTENTIAL", "INVESTMENT_REVIEW", "LAUNCH_REVIEW", "USER_VALIDATION"}


def test_agent_handoff_v3_accepts_integrity_bound_finding_and_rejects_wide_claim() -> None:
    validator = _validator("handoffs/agent-handoff.v3.json")
    evidence_ref = {"ref": f"evidence:{uuid4()}", "sha256": "a" * 64}
    handoff = {
        "schema_version": "3.0",
        "tenant_id": str(uuid4()),
        "run_id": str(uuid4()),
        "task_id": str(uuid4()),
        "dispatch_epoch": 0,
        "agent_code": "user-evidence",
        "status": "SUCCEEDED",
        "findings": [{
            "finding_id": str(uuid4()),
            "agent_code": "user-evidence",
            "dimension": "user_value",
            "subdimension": "regional_fit",
            "claim": "The provided regional sample supports the bounded conclusion.",
            "grade": "MODERATE",
            "score_input": 3,
            "evidence_refs": [evidence_ref["ref"]],
            "confidence": 0.7,
            "limitations": ["small sample"],
            "region_scope": ["HK"],
            "as_of": "2026-08-11",
            "valid_until": "2027-02-11",
            "hypothesis": False,
            "report_section_ref": "section:user-value",
        }],
        "report_ref": {"ref": f"report:{uuid4()}", "sha256": "b" * 64},
        "evidence_refs": [evidence_ref],
        "limitations": [],
        "confidence": 0.7,
        "failure_class": None,
        "next_action": "Submit findings for audit.",
    }
    validator.validate(handoff)
    invalid = json.loads(json.dumps(handoff))
    invalid["findings"][0]["unbounded_claims"] = [{}]
    with pytest.raises(ValidationError):
        validator.validate(invalid)


def test_round_caps_and_v4_topology_are_contract_enforced() -> None:
    audit = _validator("audit/audit-request.v3.json")
    with pytest.raises(ValidationError):
        audit.validate({
            "schema_version": "3.0",
            "request_id": str(uuid4()),
            "run_id": str(uuid4()),
            "audit_round": 3,
            "finding_ids": [str(uuid4())],
            "report_refs": [],
            "evidence_refs": [],
            "score_profile_ref": "score-profile:full-potential@1.0",
        })
    manifest = _validator("run-manifest/run-manifest.v4.json")
    invalid = {
        "schema_version": "4.0",
        "architecture_generation": "supervisor-1p4-v1",
        "feature_flag": "LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED",
        "physical_topology": {
            "worker_count": 6,
            "leader": "evaluation-manager",
            "workers": ["user-evidence", "product-engineering", "business-investment", "evidence-auditor"],
            "peer_mentions": False,
        },
        "agent_contract_generation": "v4",
        "agents": {},
        "contracts": {},
        "score_profile": {"version": "1.0", "sha256": "a" * 64},
        "skills": {},
        "tools": {},
        "budget": {},
        "limits": {"targeted_remediation_rounds": 1, "reaudit_rounds": 1},
        "failure_policy": {
            "SUBMISSION_UNKNOWN": "NEEDS_ATTENTION_NO_RETRY",
            "USAGE_UNKNOWN": "NEEDS_ATTENTION_NO_RETRY",
            "BILLING_UNKNOWN": "NEEDS_ATTENTION_NO_RETRY",
            "PAID_TIMEOUT": "NEEDS_ATTENTION_NO_RETRY",
        },
    }
    with pytest.raises(ValidationError):
        manifest.validate(invalid)
