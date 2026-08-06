from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from launchscope_skills import P0_SKILL_CODES, SkillContractError, SkillRegistry


def test_all_six_p0_skill_contracts_load_and_validate() -> None:
    registry = SkillRegistry()
    manifests = registry.load_p0()
    assert {manifest.skill_code for manifest in manifests} == P0_SKILL_CODES
    product_intake = next(manifest for manifest in manifests if manifest.skill_code == "product-intake-normalizer")
    product_intake.validate_input({"product_version_id": "x", "material_ids": ["y"]})
    with pytest.raises(SkillContractError):
        product_intake.validate_output({"profile_draft": {}})


def test_same_skill_version_cannot_be_reloaded_with_a_different_hash(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "manifests"
    target = tmp_path / "manifests"
    shutil.copytree(source, target)
    registry = SkillRegistry(target)
    original = target / "product-intake-normalizer" / "1.0.json"
    registry.load_file(original)
    modified = json.loads(original.read_text(encoding="utf-8"))
    modified["budget"]["max_cost"] = 1
    without_hash = {key: value for key, value in modified.items() if key != "content_sha256"}
    modified["content_sha256"] = hashlib.sha256(
        json.dumps(without_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    original.write_text(json.dumps(modified), encoding="utf-8")
    with pytest.raises(SkillContractError, match="version lock conflict"):
        registry.load_file(original)
