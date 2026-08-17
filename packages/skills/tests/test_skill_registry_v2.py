from __future__ import annotations

from launchscope_skills import P0_SKILL_CODES_V2, SkillRegistry


def test_v2_catalog_promotes_user_validation_and_evidence_audit() -> None:
    manifests = SkillRegistry().load_p0_v2()

    assert {manifest.skill_code for manifest in manifests} == P0_SKILL_CODES_V2
    uvd = next(manifest for manifest in manifests if manifest.skill_code == "user-validation-designer")
    audit = next(manifest for manifest in manifests if manifest.skill_code == "evidence-grounding-audit")
    assert audit.version == "2.0"
    uvd.validate_input(
        {
            "task_id": "task-1",
            "project_id": "project-1",
            "product_version": "V1",
            "product_profile": {"name": "Demo", "one_line_value_claim": "Reduce repeated work"},
            "target_users": {"raw_description": "weekly operators with a deadline and a manual spreadsheet"},
            "validation_goal": {"objective": "test recurring demand"},
        }
    )


def test_v2_catalog_preserves_the_legacy_six_skill_generation() -> None:
    registry = SkillRegistry()

    assert {manifest.skill_code for manifest in registry.load_p0()} != P0_SKILL_CODES_V2


def test_v2_and_v3_catalogs_select_exact_user_validation_versions() -> None:
    registry = SkillRegistry()

    v2 = next(item for item in registry.load_p0_v2() if item.skill_code == "user-validation-designer")
    v3 = next(item for item in registry.load_p0_v3() if item.skill_code == "user-validation-designer")

    assert v2.version == "1.0.4"
    assert v3.version == "1.0.5"
    assert v2.content_sha256 != v3.content_sha256
    assert v3.output_contract["$id"] == "launchscope://skills/user-validation-designer/0.2/output"
