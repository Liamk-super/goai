"""Pre-run Harness: validate and freeze the reproducible execution contract."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import UUID

from launchscope_domain import BudgetReservation, EvaluationRun, RunManifest
from launchscope_skills import P0_SKILL_CODES, SkillRegistry

from .manifest_loader import AGENT_CODES, AgentIdentityContract, AgentManifestLoader

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FAILURE_CLASSES = frozenset(
    {
        "TRANSIENT",
        "VALIDATION",
        "AUTHORIZATION",
        "DEPENDENCY",
        "BUDGET",
        "POLICY",
        "SUBMISSION_UNKNOWN",
        "BUSINESS",
    }
)


class HarnessValidationError(ValueError):
    """The submitted execution configuration cannot safely start a Run."""


@dataclass(frozen=True, slots=True)
class HarnessSpec:
    product_version_id: UUID | str
    material_hashes: Mapping[UUID | str, str]
    standard_version: str
    prompt_versions: Mapping[str, str]
    model_versions: Mapping[str, str]
    tool_versions: Mapping[str, str]
    budget_limits: tuple[BudgetReservation, ...]
    permissions: tuple[str, ...]
    timeout_seconds: int
    regions: tuple[str, ...]
    data_as_of: datetime
    approval_points: tuple[str, ...]
    failure_policy: Mapping[str, str]
    evidence_requirements: Mapping[str, tuple[str, ...]]
    security_policy_version: str = "1.0"


class RunHarness:
    """Builds the one immutable Manifest allowed to enter execution."""

    def __init__(
        self,
        *,
        agents: AgentManifestLoader | None = None,
        skills: SkillRegistry | None = None,
    ) -> None:
        self.agents = agents or AgentManifestLoader()
        self.skills = skills or SkillRegistry()

    def validate(self, spec: HarnessSpec, *, run_id: UUID | str | None = None) -> tuple[
        tuple[AgentIdentityContract, ...], Mapping[str, str]
    ]:
        product_version_id = _uuid(spec.product_version_id, "product_version_id")
        if not spec.material_hashes:
            raise HarnessValidationError("at least one material hash is required before a Run starts")
        normalized_materials = {
            _uuid(key, "material_id"): _sha256(value, "material hash") for key, value in spec.material_hashes.items()
        }
        if len(normalized_materials) != len(spec.material_hashes):
            raise HarnessValidationError("material ids must be unique")
        _version(spec.standard_version, "standard_version")
        _version(spec.security_policy_version, "security_policy_version")
        if not isinstance(spec.timeout_seconds, int) or spec.timeout_seconds <= 0:
            raise HarnessValidationError("timeout_seconds must be positive")
        if not spec.regions or any(not isinstance(region, str) or not region.strip() for region in spec.regions):
            raise HarnessValidationError("at least one non-empty region is required")
        if spec.data_as_of.tzinfo is None or spec.data_as_of.utcoffset() is None:
            raise HarnessValidationError("data_as_of must be timezone-aware")
        if len(set(spec.permissions)) != len(spec.permissions) or any(not item.strip() for item in spec.permissions):
            raise HarnessValidationError("permissions must be non-empty and unique")
        for name, versions in (
            ("prompt_versions", spec.prompt_versions),
            ("model_versions", spec.model_versions),
            ("tool_versions", spec.tool_versions),
        ):
            if not versions or any(
                not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip()
                for key, value in versions.items()
            ):
                raise HarnessValidationError(f"{name} must freeze non-empty component versions")
        if not spec.approval_points or any(not item.strip() for item in spec.approval_points):
            raise HarnessValidationError("approval_points must be explicit")
        if set(spec.failure_policy) != _FAILURE_CLASSES or any(
            not value.strip() for value in spec.failure_policy.values()
        ):
            raise HarnessValidationError("failure_policy must cover every closed FailureClass")
        if spec.failure_policy.get("SUBMISSION_UNKNOWN") != "NEEDS_ATTENTION":
            raise HarnessValidationError("SUBMISSION_UNKNOWN must fail closed to NEEDS_ATTENTION")
        if set(spec.evidence_requirements) != P0_SKILL_CODES or any(
            not values for values in spec.evidence_requirements.values()
        ):
            raise HarnessValidationError("evidence requirements must cover all six P0 Skills")
        if run_id is not None and any(
            reservation.run_id != _uuid(run_id, "run_id") for reservation in spec.budget_limits
        ):
            raise HarnessValidationError("budget reservations must belong to the Run")
        if not spec.budget_limits:
            raise HarnessValidationError("at least one budget reservation is required")
        contracts = self.agents.load_all()
        manifests = self.skills.load_p0()
        skill_hashes = {manifest.skill_code: manifest.content_sha256 for manifest in manifests}
        if set(skill_hashes) != P0_SKILL_CODES:
            raise HarnessValidationError("the frozen Harness requires exactly six P0 Skill hashes")
        if {contract.code for contract in contracts} != AGENT_CODES:
            raise HarnessValidationError("the frozen Harness requires the fixed 1+5 Agent team")
        if _uuid(spec.product_version_id, "product_version_id") != product_version_id:
            raise HarnessValidationError("invalid ProductVersion")
        return contracts, MappingProxyType(skill_hashes)

    def build_manifest(self, spec: HarnessSpec, *, run_id: UUID | str | None = None) -> RunManifest:
        contracts, skill_hashes = self.validate(spec, run_id=run_id)
        material_hashes = {
            str(_uuid(key, "material_id")): _sha256(value, "material hash")
            for key, value in spec.material_hashes.items()
        }
        agent_versions = {contract.code: f"{contract.version}:{contract.content_sha256}" for contract in contracts}
        configuration: Mapping[str, object] = MappingProxyType(
            {
                "product_version_id": str(_uuid(spec.product_version_id, "product_version_id")),
                "material_hashes": dict(sorted(material_hashes.items())),
                "agent_contract_hashes": {contract.code: contract.content_sha256 for contract in contracts},
                "regions": tuple(sorted(spec.regions)),
                "data_as_of": spec.data_as_of.astimezone(UTC).isoformat(),
                "approval_points": tuple(sorted(spec.approval_points)),
                "failure_policy": dict(sorted(spec.failure_policy.items())),
                "evidence_requirements": {
                    code: tuple(sorted(values)) for code, values in sorted(spec.evidence_requirements.items())
                },
            }
        )
        return RunManifest(
            standard_version=spec.standard_version,
            material_ids=tuple(_uuid(key, "material_id") for key in spec.material_hashes),
            agent_versions=agent_versions,
            skill_versions=skill_hashes,
            prompt_versions=dict(spec.prompt_versions),
            model_versions=dict(spec.model_versions),
            tool_versions=dict(spec.tool_versions),
            budget_limits=spec.budget_limits,
            permissions=spec.permissions,
            timeout_seconds=spec.timeout_seconds,
            security_policy_version=spec.security_policy_version,
            configuration=configuration,
        ).freeze()

    def freeze_for_run(self, run: EvaluationRun, spec: HarnessSpec) -> RunManifest:
        if run.product_version_id != _uuid(spec.product_version_id, "product_version_id"):
            raise HarnessValidationError("Harness ProductVersion does not match the Run")
        if run.standard_version != spec.standard_version:
            raise HarnessValidationError("Harness standard_version does not match the Run")
        return run.freeze_manifest(self.build_manifest(spec, run_id=run.run_id))


def _uuid(value: UUID | str, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise HarnessValidationError(f"{field_name} must be a UUID") from exc


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise HarnessValidationError(f"{field_name} must be a lower-case SHA-256")
    return value


def _version(value: object, field_name: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+\.[0-9]+", value) is None:
        raise HarnessValidationError(f"{field_name} must use MAJOR.MINOR form")


__all__ = ["HarnessSpec", "HarnessValidationError", "RunHarness"]
