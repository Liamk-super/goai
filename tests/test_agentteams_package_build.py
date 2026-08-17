from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType


def _build_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "build-agentteams-packages.py"
    spec = importlib.util.spec_from_file_location("build_agentteams_packages", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v6_manager_package_binds_planning_and_synthesis_to_the_v22_contracts() -> None:
    module = _build_module()
    module.GENERATION = "v6"
    files = module.package_files(
        {
            "code": "evaluation-manager",
            "version": "6.0",
            "schema_version": "6.0",
            "content_sha256": "a" * 64,
            "responsibilities": [],
            "allowed_tools": [],
        },
        "launchscope-evaluation-supervisor-v6-live",
    )

    skill = files["skills/launchscope-evaluation-manager-handoff-v3/SKILL.md"]
    assert json.loads(files["manifest.json"])["version"] == "6.0.14"
    assert "ManagerPlanV2" in skill
    assert "score-profile:full-potential@1.0" in skill
    assert "copy dispatch_epoch" in skill.lower()
    assert "Copy message_type exactly" in skill
    assert "When the assignment requests ManagerSynthesisV2" in skill
    assert "return only one ManagerSynthesisV2 JSON object" not in skill


def test_all_v6_worker_packages_bump_runtime_version_when_embedded_skills_change() -> None:
    module = _build_module()
    module.GENERATION = "v6"
    files = module.package_files(
        {
            "code": "product-engineering",
            "version": "6.0",
            "schema_version": "6.0",
            "content_sha256": "b" * 64,
            "responsibilities": [],
            "allowed_tools": ["launchscope-context.get.v2"],
            "allowed_skills": ["product-technical-audit"],
        },
        "launchscope-product-engineering-v6-live",
    )

    assert json.loads(files["manifest.json"])["version"] == "6.0.14"
    assert "skills/_shared/report-source-normalization.mjs" in files


def test_v6_worker_packages_call_mcp_without_model_token_transcription() -> None:
    module = _build_module()
    module.GENERATION = "v6"
    files = module.package_files(
        {
            "code": "product-engineering",
            "version": "6.0",
            "schema_version": "6.0",
            "content_sha256": "c" * 64,
            "responsibilities": [],
            "allowed_tools": ["launchscope-context.get.v2"],
            "allowed_skills": ["product-technical-audit"],
        },
        "launchscope-product-engineering-v6-live",
    )

    skill_path = "skills/launchscope-product-engineering-handoff-v3/SKILL.md"
    helper_path = "skills/launchscope-product-engineering-handoff-v3/scripts/launchscope_mcp_call.py"
    assert helper_path in files
    assert "launchscope_mcp_call.py" in files[skill_path]
    assert "current Task's MCP calls" in files[skill_path]
    assert "every required material_scope" in files[skill_path]
    assert "current Matrix channel" in files[skill_path]
    assert "top-level tool_allowlist" in files[skill_path]
    assert "--read-required-materials" in files[skill_path]
    assert "top-level `specialist_report` field" in files[skill_path]
    assert "Do not leave the built report only in a local file" in files[skill_path]
    assert '"context_token":"<exact context_token>"' not in files[skill_path]
