from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "build-agentteams-packages.py"
    spec = importlib.util.spec_from_file_location("build_agentteams_packages", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evidence_auditor_package_contains_executable_skill_runtime() -> None:
    module = _module()
    contracts = module.load_contracts()
    files = module.package_files(contracts["evidence-auditor"])
    assert "skills/evidence-grounding-audit/SKILL.md" in files
    assert "skills/evidence-grounding-audit/runner/cli.mjs" in files
    assert "skills/evidence-grounding-audit/src/index.mjs" in files
    assert "skills/evidence-grounding-audit/schema/output.schema.json" in files
