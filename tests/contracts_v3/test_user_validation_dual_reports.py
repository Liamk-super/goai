from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from launchscope_skills import SkillRegistry

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"


def test_v3_manifest_and_uvd_output_schema_are_independently_valid() -> None:
    run_manifest = json.loads(
        (CONTRACTS / "run-manifest" / "run-manifest.v3.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(run_manifest)

    uvd = next(
        item for item in SkillRegistry().load_p0_v3() if item.skill_code == "user-validation-designer"
    )
    assert uvd.version == "1.0.5"
    assert uvd.document["runner_sha256"] == run_manifest["properties"]["user_validation"]["properties"][
        "runner_sha256"
    ]["const"]
    assert uvd.document["output_schema_sha256"] == run_manifest["properties"]["user_validation"][
        "properties"
    ]["output_schema_sha256"]["const"]


def test_v3_openapi_preserves_v2_paths_and_adds_report_projection() -> None:
    v2 = yaml.safe_load((CONTRACTS / "openapi" / "user-validation.v2.yaml").read_text(encoding="utf-8"))
    v3 = yaml.safe_load((CONTRACTS / "openapi" / "user-validation.v3.yaml").read_text(encoding="utf-8"))

    assert set(v2["paths"]) < set(v3["paths"])
    report_path = v3["paths"]["/runs/{runId}/user-validation-reports/{variant}"]["get"]
    assert report_path["responses"]["200"]["headers"]["Cache-Control"]["schema"]["const"] == "no-store"
    assert v3["components"]["schemas"]["PresentationMetadata"]["properties"]["version"]["const"] == "0.4"


def test_v2_artifacts_remain_locked_to_user_validation_1_0_4() -> None:
    v2_manifest = json.loads(
        (CONTRACTS / "run-manifest" / "run-manifest.v2.json").read_text(encoding="utf-8")
    )
    v2_skill = next(
        item for item in SkillRegistry().load_p0_v2() if item.skill_code == "user-validation-designer"
    )

    assert v2_manifest["properties"]["user_validation"]["properties"]["skill_version"]["const"] == "1.0.4"
    assert v2_skill.version == "1.0.4"
