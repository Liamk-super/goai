"""Content-addressed immutable contracts for the fixed LaunchScope 1+5 team."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import yaml

AGENT_CODES = frozenset(
    {
        "evaluation-manager",
        "product-engineering",
        "user-evidence",
        "business-investment",
        "geo-policy-trend",
        "evidence-auditor",
    }
)
MANAGER_CODE = "evaluation-manager"
SUPERVISOR_1P4_AGENT_CODES = frozenset(
    {"evaluation-manager", "product-engineering", "user-evidence", "business-investment", "evidence-auditor"}
)
_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "code",
        "version",
        "role",
        "responsibilities",
        "inputs",
        "outputs",
        "allowed_skills",
        "allowed_tools",
        "risk_boundaries",
        "prohibited_actions",
        "content_sha256",
    }
)
_FORBIDDEN_WRITES = frozenset({"run.write", "task.write", "memory.write", "report.write"})
# ADR 0004 adds a second published generation through Expand-Migrate-Contract.
# v1 stays the default so already frozen RunManifest hashes keep verifying.
_GENERATIONS: Mapping[str, str] = MappingProxyType(
    {"v1": "1.0", "v2": "2.0", "v3": "3.0", "v4": "4.0", "v5": "5.0", "v6": "6.0"}
)


class AgentContractError(ValueError):
    """An Agent identity contract is malformed, changed, or unsafe."""


@dataclass(frozen=True, slots=True)
class AgentIdentityContract:
    code: str
    version: str
    role: str
    responsibilities: tuple[str, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    allowed_skills: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    risk_boundaries: tuple[str, ...]
    prohibited_actions: tuple[str, ...]
    content_sha256: str
    document: Mapping[str, Any]

    @property
    def ref(self) -> str:
        return f"{self.code}@{self.version}"

    def permits_skill(self, skill_code: str) -> bool:
        return skill_code in self.allowed_skills

    def permits_tools(self, tools: tuple[str, ...]) -> bool:
        return set(tools).issubset(self.allowed_tools)


class AgentManifestLoader:
    """Loads exactly one Manager and the five fixed specialist identities."""

    def __init__(self, manifest_root: Path | None = None) -> None:
        self.manifest_root = manifest_root or Path(__file__).resolve().parents[4] / "packages" / "contracts" / "agents"
        self._by_ref: dict[tuple[str, str], AgentIdentityContract] = {}

    def load_all(self, generation: str = "v1") -> tuple[AgentIdentityContract, ...]:
        if generation not in _GENERATIONS:
            raise AgentContractError(f"unknown Agent contract generation: {generation}")
        root = (
            self.manifest_root.parent / "manager" / "agents"
            if generation in {"v5", "v6"} and self.manifest_root.name == "agents"
            else self.manifest_root
        )
        contracts = tuple(self.load_file(path) for path in sorted(root.glob(f"*.{generation}.yaml")))
        expected_codes = SUPERVISOR_1P4_AGENT_CODES if generation in {"v4", "v5", "v6"} else AGENT_CODES
        actual = {contract.code for contract in contracts}
        if actual != expected_codes or len(contracts) != len(expected_codes):
            raise AgentContractError("the Agent catalog does not match its frozen physical topology")
        manager = next(contract for contract in contracts if contract.code == MANAGER_CODE)
        if manager.role != "manager":
            raise AgentContractError("evaluation-manager must be the sole Manager")
        if any(contract.role != "specialist" for contract in contracts if contract.code != MANAGER_CODE):
            raise AgentContractError("all non-manager identities must be specialists")
        return contracts

    def load_file(self, path: Path) -> AgentIdentityContract:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise AgentContractError(f"cannot load Agent contract {path}") from exc
        if not isinstance(document, dict) or set(document) != _REQUIRED_FIELDS:
            raise AgentContractError(f"Agent contract {path.name} has an incompatible field set")
        self._validate_document(document, path)
        expected = _hash_contract(document)
        if not hmac.compare_digest(expected, document["content_sha256"]):
            raise AgentContractError(f"Agent contract hash mismatch for {document['code']}@{document['version']}")
        contract = AgentIdentityContract(
            code=document["code"],
            version=document["version"],
            role=document["role"],
            responsibilities=tuple(document["responsibilities"]),
            inputs=tuple(document["inputs"]),
            outputs=tuple(document["outputs"]),
            allowed_skills=tuple(document["allowed_skills"]),
            allowed_tools=tuple(document["allowed_tools"]),
            risk_boundaries=tuple(document["risk_boundaries"]),
            prohibited_actions=tuple(document["prohibited_actions"]),
            content_sha256=expected,
            document=MappingProxyType(dict(document)),
        )
        ref = (contract.code, contract.version)
        prior = self._by_ref.get(ref)
        if prior is not None and prior.content_sha256 != contract.content_sha256:
            raise AgentContractError(f"Agent identity version lock conflict for {contract.ref}")
        self._by_ref[ref] = contract
        return contract

    def resolve(self, code: str, version: str, expected_sha256: str) -> AgentIdentityContract:
        contract = self._by_ref.get((code, version))
        if contract is None:
            raise AgentContractError(f"Agent identity is not loaded: {code}@{version}")
        if not hmac.compare_digest(contract.content_sha256, expected_sha256):
            raise AgentContractError(f"Agent identity hash does not match frozen RunManifest: {code}@{version}")
        return contract

    @staticmethod
    def _validate_document(document: Mapping[str, object], path: Path) -> None:
        if document["schema_version"] not in set(_GENERATIONS.values()) or document["role"] not in {
            "manager",
            "specialist",
        }:
            raise AgentContractError(f"Agent contract {path.name} has an unsupported schema or role")
        for name in ("code", "version", "content_sha256"):
            if not isinstance(document[name], str) or not document[name]:
                raise AgentContractError(f"Agent contract {path.name} requires a non-empty {name}")
        if document["code"] not in AGENT_CODES or document["version"] != document["schema_version"]:
            raise AgentContractError(f"Agent contract {path.name} is not a fixed LaunchScope identity")
        if not isinstance(document["content_sha256"], str) or len(document["content_sha256"]) != 64:
            raise AgentContractError(f"Agent contract {path.name} has an invalid content_sha256")
        for name in (
            "responsibilities",
            "inputs",
            "outputs",
            "allowed_skills",
            "allowed_tools",
            "risk_boundaries",
            "prohibited_actions",
        ):
            value = document[name]
            if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
                raise AgentContractError(f"Agent contract {path.name} has an invalid {name}")
        prohibited_actions = cast(list[str], document["prohibited_actions"])
        risk_boundaries = cast(list[str], document["risk_boundaries"])
        if not _FORBIDDEN_WRITES.issubset(prohibited_actions):
            raise AgentContractError(f"Agent contract {path.name} must prohibit direct control-plane writes")
        if document["role"] == "manager" and "does_not_make_specialist_conclusions" not in risk_boundaries:
            raise AgentContractError("Manager must be prohibited from manufacturing specialist conclusions")


def _hash_contract(document: Mapping[str, object]) -> str:
    canonical = {key: value for key, value in document.items() if key != "content_sha256"}
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "AGENT_CODES",
    "MANAGER_CODE",
    "SUPERVISOR_1P4_AGENT_CODES",
    "AgentContractError",
    "AgentIdentityContract",
    "AgentManifestLoader",
]
