from __future__ import annotations

import json
import runpy
from pathlib import Path

from launchscope_skills import REPORT_V22_SKILL_CODES, SkillRegistry

ROOT = Path(__file__).resolve().parents[3]


def test_report_v22_catalog_is_exact_and_uses_one_specialist_report_contract() -> None:
    manifests = SkillRegistry().load_report_v22()

    assert {item.skill_code for item in manifests} == REPORT_V22_SKILL_CODES
    assert {(item.skill_code, item.version) for item in manifests} == {
        ("user-validation-designer", "1.1.0"),
        ("product-technical-audit", "1.0.0"),
        ("business-investment-assessment", "2.0.0"),
        ("evidence-grounding-audit", "2.2.0"),
    }
    assert {item.output_contract["$id"] for item in manifests} == {
        "https://launchscope.local/contracts/reports/specialist-report.v2.json"
    }
    assert all(item.document["runtime_entrypoint"].endswith("runner/cli.mjs") for item in manifests)
    assert all(item.document["may_write_business_state"] is False for item in manifests)


def test_report_v22_manifests_pin_existing_runtime_and_schema_files() -> None:
    for manifest in SkillRegistry().load_report_v22():
        package_root = ROOT / "packages" / manifest.skill_code
        assert package_root.is_dir()
        assert (package_root / "SKILL.md").is_file()
        assert (package_root / "package.json").is_file()
        assert (package_root / manifest.document["runtime_entrypoint"]).is_file()
        assert (package_root / "schema/specialist-report.v2.schema.json").is_file()


def test_v6_agentteams_packages_contain_real_specialist_runtimes() -> None:
    namespace = runpy.run_path(str(ROOT / "scripts/build-agentteams-packages.py"))
    namespace["package_files"].__globals__["GENERATION"] = "v6"
    expected = {
        "user-evidence": "user-validation-designer",
        "product-engineering": "product-technical-audit",
        "business-investment": "business-investment-assessment",
        "evidence-auditor": "evidence-grounding-audit",
    }
    for agent_code, skill_code in expected.items():
        contract = next(item for item in namespace["load_contracts"]().values() if item["code"] == agent_code)
        files = namespace["package_files"](contract)
        assert f"skills/{skill_code}/runner/cli.mjs" in files
        assert f"skills/{skill_code}/schema/specialist-report.v2.schema.json" in files
        binding = files[f"skills/{skill_code}/LAUNCHSCOPE.md"]
        assert "SpecialistReportDocumentV2" in binding
        assert "assigned material" in binding.lower()
        assert "manual JSON fallback" in binding


def test_manifest_schema_rejects_html_as_canonical_report_truth() -> None:
    schema = json.loads((ROOT / "packages/skills/manifests-v3/skill-manifest.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["canonical_output"]["const"] == "SpecialistReportDocumentV2"
    assert schema["properties"]["projection_policy"]["properties"]["html_is_canonical"]["const"] is False
