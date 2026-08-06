"""Versioned P0 Skill manifest loading, schema validation and hash locking."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

P0_SKILL_CODES = frozenset(
    {
        "product-intake-normalizer",
        "intake-gap-diagnosis",
        "browser-product-audit",
        "business-investment-assessment",
        "evidence-grounding-audit",
        "version-regression-verification",
    }
)


class SkillContractError(ValueError):
    """A manifest is malformed, altered, or incompatible with its pinned version."""


@dataclass(frozen=True, slots=True)
class SkillManifest:
    skill_code: str
    version: str
    content_sha256: str
    document: Mapping[str, Any]

    def validate_input(self, value: object) -> None:
        _validate_instance(self.document["input_schema"], value, "input")

    def validate_output(self, value: object) -> None:
        _validate_instance(self.document["output_schema"], value, "output")


class SkillRegistry:
    """Loads only exact, content-addressed Skill versions from package artifacts."""

    def __init__(self, manifest_root: Path | None = None) -> None:
        self.manifest_root = manifest_root or Path(__file__).resolve().parents[2] / "manifests"
        self._by_ref: dict[tuple[str, str], SkillManifest] = {}

    def load_p0(self) -> tuple[SkillManifest, ...]:
        manifests = tuple(
            self.load_file(path)
            for path in sorted(self.manifest_root.glob("*/*.json"))
            if path.name != "skill-manifest.schema.json"
        )
        actual = {manifest.skill_code for manifest in manifests}
        if actual != P0_SKILL_CODES or len(manifests) != len(P0_SKILL_CODES):
            raise SkillContractError("the P0 catalog must contain exactly the six frozen Skill manifests")
        return manifests

    def load_file(self, path: Path) -> SkillManifest:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillContractError(f"cannot load Skill manifest {path}") from exc
        schema = json.loads((self.manifest_root / "skill-manifest.schema.json").read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(schema).iter_errors(document), key=lambda item: item.json_path)
        if errors:
            raise SkillContractError(f"manifest {path.name} violates contract: {errors[0].message}")
        for field_name in ("input_schema", "output_schema"):
            try:
                Draft202012Validator.check_schema(document[field_name])
            except Exception as exc:  # jsonschema exposes several validation exception types.
                raise SkillContractError(f"manifest {path.name} has invalid {field_name}") from exc
        expected = _hash_manifest(document)
        if not _constant_time_equal(expected, document["content_sha256"]):
            raise SkillContractError(f"manifest hash mismatch for {document['skill_code']}@{document['version']}")
        manifest = SkillManifest(document["skill_code"], document["version"], expected, document)
        ref = (manifest.skill_code, manifest.version)
        prior = self._by_ref.get(ref)
        if prior is not None and prior.content_sha256 != manifest.content_sha256:
            raise SkillContractError(f"Skill version lock conflict for {manifest.skill_code}@{manifest.version}")
        self._by_ref[ref] = manifest
        return manifest

    def resolve(self, skill_code: str, version: str, expected_sha256: str) -> SkillManifest:
        manifest = self._by_ref.get((skill_code, version))
        if manifest is None:
            raise SkillContractError(f"Skill version is not loaded: {skill_code}@{version}")
        if not _constant_time_equal(manifest.content_sha256, expected_sha256):
            raise SkillContractError(f"Skill version hash does not match frozen RunManifest: {skill_code}@{version}")
        return manifest


def _hash_manifest(document: Mapping[str, Any]) -> str:
    canonical = {key: value for key, value in document.items() if key != "content_sha256"}
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(serialized).hexdigest()


def _validate_instance(schema: object, value: object, label: str) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: item.json_path)
    if errors:
        raise SkillContractError(f"Skill {label} does not satisfy JSON Schema: {errors[0].message}")


def _constant_time_equal(left: str, right: str) -> bool:
    import hmac

    return isinstance(right, str) and hmac.compare_digest(left, right)


__all__ = ["P0_SKILL_CODES", "SkillContractError", "SkillManifest", "SkillRegistry"]
