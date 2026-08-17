from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from launchscope_orchestrator.agentteams_bridge import AgentHandoffV2

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"


def test_additive_user_validation_json_schemas_are_valid() -> None:
    paths = [
        CONTRACTS / "handoffs" / "agent-handoff.v2.json",
        CONTRACTS / "audit" / "audit-request.v2.json",
        CONTRACTS / "audit" / "audit-result.v2.json",
        CONTRACTS / "run-manifest" / "run-manifest.v2.json",
        *(CONTRACTS / "tools").glob("user-validation-*.v1.json"),
        *(CONTRACTS / "tools").glob("user-validation-designer.*.v1.json"),
    ]
    assert paths
    for path in paths:
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_write_tools_require_revision_hash_idempotency_correlation_and_capability() -> None:
    required = {
        "expected_revision",
        "checkpoint_sha256",
        "idempotency_key",
        "correlation_id",
        "context_token",
    }
    for name in (
        "user-validation-designer.start.v1.json",
        "user-validation-designer.submit-step.v1.json",
        "user-validation-designer.resume.v1.json",
    ):
        document = json.loads((CONTRACTS / "tools" / name).read_text(encoding="utf-8"))
        assert required <= set(document["properties"]["input_schema"]["required"])


def test_handoff_v2_has_only_integrity_bound_user_result_fields() -> None:
    properties = AgentHandoffV2.model_json_schema()["properties"]
    assert {"skill_result_ref", "skill_result_sha256", "validation_mode"} <= set(properties)
    assert "full_report" not in properties
    assert "checkpoint" not in properties
    assert "model_log" not in properties


def test_openapi_exposes_all_four_additive_rest_resources() -> None:
    document = yaml.safe_load((CONTRACTS / "openapi" / "user-validation.v2.yaml").read_text(encoding="utf-8"))
    assert set(document["paths"]) == {
        "/product-versions/{versionId}/user-validation-script",
        "/product-versions/{versionId}/user-evidence",
        "/runs/{baselineRunId}/user-evidence-rechecks",
        "/runs/{runId}/user-validation-result",
    }
    assert document["components"]["schemas"]["ProductValidationScript"]["properties"]["tasks"]["maxItems"] == 5


def test_v1_contracts_remain_loadable() -> None:
    for path in (
        CONTRACTS / "commands" / "run-commands.v1.json",
        CONTRACTS / "events" / "evaluation-events.v1.json",
        CONTRACTS / "unified-model" / "launchscope-unified-model.v1.json",
    ):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
