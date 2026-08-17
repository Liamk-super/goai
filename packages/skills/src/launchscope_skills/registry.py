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
P0_SKILL_CODES_V2 = frozenset({*P0_SKILL_CODES, "user-validation-designer"})
REPORT_V22_SKILL_CODES = frozenset(
    {
        "user-validation-designer",
        "product-technical-audit",
        "business-investment-assessment",
        "evidence-grounding-audit",
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
    input_contract: Mapping[str, Any]
    output_contract: Mapping[str, Any]

    def validate_input(self, value: object) -> None:
        _validate_instance(self.input_contract, value, "input")

    def validate_output(self, value: object) -> None:
        _validate_instance(self.output_contract, value, "output")


class SkillRegistry:
    """Loads only exact, content-addressed Skill versions from package artifacts."""

    def __init__(
        self,
        manifest_root: Path | None = None,
        manifest_v2_root: Path | None = None,
        manifest_v3_root: Path | None = None,
    ) -> None:
        self.manifest_root = manifest_root or Path(__file__).resolve().parents[2] / "manifests"
        self.manifest_v2_root = manifest_v2_root or Path(__file__).resolve().parents[2] / "manifests-v2"
        self.manifest_v3_root = manifest_v3_root or Path(__file__).resolve().parents[2] / "manifests-v3"
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

    def load_p0_v2(self) -> tuple[SkillManifest, ...]:
        return self._load_promoted_catalog("1.0.4", "V2")

    def load_p0_v3(self) -> tuple[SkillManifest, ...]:
        return self._load_promoted_catalog("1.0.5", "V3")

    def load_report_v22(self) -> tuple[SkillManifest, ...]:
        manifests = tuple(
            self.load_file(self.manifest_v3_root / skill_code / version)
            for skill_code, version in (
                ("user-validation-designer", "1.1.0.json"),
                ("product-technical-audit", "1.0.0.json"),
                ("business-investment-assessment", "2.0.0.json"),
                ("evidence-grounding-audit", "2.2.0.json"),
            )
        )
        actual = {manifest.skill_code for manifest in manifests}
        if actual != REPORT_V22_SKILL_CODES or len(manifests) != len(REPORT_V22_SKILL_CODES):
            raise SkillContractError("the report v2.2 catalog must contain exactly the four specialist Skills")
        return manifests

    def _load_promoted_catalog(self, user_validation_version: str, generation: str) -> tuple[SkillManifest, ...]:
        manifests = (
            *(manifest for manifest in self.load_p0() if manifest.skill_code != "evidence-grounding-audit"),
            self.load_file(self.manifest_v2_root / "evidence-grounding-audit" / "2.0.json"),
            self.load_file(self.manifest_v2_root / "user-validation-designer" / f"{user_validation_version}.json"),
        )
        actual = {manifest.skill_code for manifest in manifests}
        if actual != P0_SKILL_CODES_V2 or len(manifests) != len(P0_SKILL_CODES_V2):
            raise SkillContractError(
                f"the {generation} P0 catalog must contain five legacy Skills and two promoted Skills"
            )
        return manifests

    def load_file(self, path: Path) -> SkillManifest:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillContractError(f"cannot load Skill manifest {path}") from exc
        schema_root = {
            "2.0": self.manifest_v2_root,
            "3.0": self.manifest_v3_root,
        }.get(document.get("schema_version"), self.manifest_root)
        schema = json.loads((schema_root / "skill-manifest.schema.json").read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(schema).iter_errors(document), key=lambda item: item.json_path)
        if errors:
            raise SkillContractError(f"manifest {path.name} violates contract: {errors[0].message}")
        if document["schema_version"] in {"2.0", "3.0"}:
            input_contract = self._load_schema_reference(
                path, document["input_schema_ref"], document["input_schema_sha256"]
            )
            output_contract = self._load_schema_reference(
                path, document["output_schema_ref"], document["output_schema_sha256"]
            )
        else:
            input_contract = document["input_schema"]
            output_contract = document["output_schema"]
        for field_name, contract in (("input", input_contract), ("output", output_contract)):
            try:
                Draft202012Validator.check_schema(contract)
            except Exception as exc:  # jsonschema exposes several validation exception types.
                raise SkillContractError(f"manifest {path.name} has invalid {field_name} schema") from exc
        expected = _hash_manifest(document)
        if not _constant_time_equal(expected, document["content_sha256"]):
            raise SkillContractError(f"manifest hash mismatch for {document['skill_code']}@{document['version']}")
        manifest = SkillManifest(
            document["skill_code"], document["version"], expected, document, input_contract, output_contract
        )
        ref = (manifest.skill_code, manifest.version)
        prior = self._by_ref.get(ref)
        if prior is not None and prior.content_sha256 != manifest.content_sha256:
            raise SkillContractError(f"Skill version lock conflict for {manifest.skill_code}@{manifest.version}")
        self._by_ref[ref] = manifest
        return manifest

    @staticmethod
    def _load_schema_reference(path: Path, reference: str, expected_sha256: str) -> Mapping[str, Any]:
        resolved = (path.parent / reference).resolve()
        packages_root = Path(__file__).resolve().parents[3]
        try:
            resolved.relative_to(packages_root)
        except ValueError as exc:
            raise SkillContractError("V2 Skill schema reference escapes the packages directory") from exc
        try:
            payload = resolved.read_bytes()
            document = json.loads(payload)
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillContractError(f"cannot load referenced Skill schema {reference}") from exc
        actual = hashlib.sha256(payload).hexdigest()
        if not _constant_time_equal(actual, expected_sha256):
            raise SkillContractError(f"referenced Skill schema hash mismatch: {reference}")
        return document

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


__all__ = [
    "P0_SKILL_CODES",
    "P0_SKILL_CODES_V2",
    "REPORT_V22_SKILL_CODES",
    "SkillContractError",
    "SkillManifest",
    "SkillRegistry",
]
