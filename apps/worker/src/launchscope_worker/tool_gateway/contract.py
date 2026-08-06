"""Uniform, fail-closed Tool Contract enforcement at the Worker boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from launchscope_domain import FailureClass

from ..runtime.lease import LeaseRegistry


class ToolGatewayError(RuntimeError):
    """A Tool invocation violates its frozen contract or egress boundary."""


class ToolInvocationStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"


@dataclass(frozen=True, slots=True)
class ToolContract:
    tool_id: str
    version: str
    permission: str
    network_level: str
    read_only: bool
    timeout_seconds: int
    max_cost: int | float
    allowed_domains: tuple[str, ...] = ()
    max_redirects: int = 0
    max_response_bytes: int = 0
    input_required: tuple[str, ...] = ()
    input_properties: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    output_required: tuple[str, ...] = ()
    output_properties: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    invocation_id: UUID
    run_id: UUID
    task_id: UUID
    tool_id: str
    idempotency_key: str
    status: ToolInvocationStatus
    started_at: datetime
    completed_at: datetime
    manifest_sha256: str
    parameters_sha256: str
    result: Mapping[str, object]
    evidence: Mapping[str, object] | None = None
    failure_class: FailureClass | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AdapterResult:
    result: Mapping[str, object]
    evidence: Mapping[str, object] | None = None
    submission_state_known: bool = True
    cost_state_known: bool = True


ToolAdapter = Callable[[Mapping[str, object], ToolContract], AdapterResult]


class ToolContractRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).resolve().parents[5] / "packages" / "contracts" / "tools"

    def load(self, tool_id: str) -> ToolContract:
        filename = {
            "browser.read.v1": "browser.read.v1.json",
            "public-research.get.v1": "public-research.get.v1.json",
            "repository.read.v1": "repository.read.v1.json",
            "launchscope-context.get.v1": "launchscope-context.get.v1.json",
            "browser-audit.v1": "browser-audit.v1.json",
            "public-research-search.v1": "public-research-search.v1.json",
        }.get(tool_id)
        if filename is None:
            raise ToolGatewayError("unknown Tool Contract")
        try:
            document = json.loads((self.root / filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ToolGatewayError("Tool Contract is unavailable") from exc
        properties = document.get("properties", {})

        def fixed(name: str) -> object:
            value = properties.get(name, {})
            if not isinstance(value, dict) or "const" not in value:
                raise ToolGatewayError("Tool Contract lacks a fixed boundary field")
            return value["const"]

        network = document.get("x-network-policy", {})
        execution = document.get("x-execution-policy", {})
        input_schema = properties.get("input_schema", {})
        output_schema = properties.get("output_schema", {})
        if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
            raise ToolGatewayError("Tool Contract lacks input or output schemas")
        return ToolContract(
            tool_id=str(fixed("tool_id")),
            version=str(fixed("contract_version")),
            permission=str(fixed("permission")),
            network_level=str(fixed("network_level")),
            read_only=bool(fixed("read_only")),
            timeout_seconds=execution["timeout_seconds"],
            max_cost=execution["max_cost"],
            allowed_domains=tuple(network.get("allowed_domains", ())),
            max_redirects=network.get("max_redirects", 0),
            max_response_bytes=network.get("max_response_bytes", 0),
            input_required=tuple(input_schema.get("required", ())),
            input_properties=dict(input_schema.get("properties", {})),
            output_required=tuple(output_schema.get("required", ())),
            output_properties=dict(output_schema.get("properties", {})),
        )


class ToolGateway:
    """Checks frozen Harness values before the adapter can reach any transport."""

    def __init__(self, *, contracts: ToolContractRegistry | None = None, leases: LeaseRegistry | None = None) -> None:
        self.contracts = contracts or ToolContractRegistry()
        self.leases = leases or LeaseRegistry()
        self._records: dict[tuple[UUID, UUID, str, str], ToolInvocation] = {}

    def lookup(self, run_id: UUID, task_id: UUID, tool_id: str, idempotency_key: str) -> ToolInvocation | None:
        """Return a durable-equivalent idempotency record before leasing again."""
        return self._records.get((run_id, task_id, tool_id, idempotency_key))

    def invoke(
        self,
        *,
        run_id: UUID,
        task_id: UUID,
        task_tools: tuple[str, ...],
        task_timeout_seconds: int,
        task_budget: int | float,
        manifest: object,
        lease_token: str,
        tool_id: str,
        idempotency_key: str,
        parameters: Mapping[str, object],
        adapter: ToolAdapter,
    ) -> ToolInvocation:
        key = (run_id, task_id, tool_id, idempotency_key)
        existing = self._records.get(key)
        if existing is not None:
            return existing
        contract = self.contracts.load(tool_id)
        self._validate_dispatch(contract, task_tools, task_timeout_seconds, task_budget, manifest, tool_id)
        self._validate_payload(parameters, contract.input_required, contract.input_properties, "input")
        self.leases.require_active(task_id, lease_token)
        started = datetime.now(UTC)
        params_hash = hashlib.sha256(json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        try:
            adapter_result = adapter(parameters, contract)
        except ToolGatewayError as exc:
            invocation = self._failed(
                run_id, task_id, tool_id, idempotency_key, started, manifest, params_hash, str(exc), FailureClass.POLICY
            )
        except Exception:
            invocation = self._failed(
                run_id,
                task_id,
                tool_id,
                idempotency_key,
                started,
                manifest,
                params_hash,
                "tool transport failed with unknown state",
                FailureClass.SUBMISSION_UNKNOWN,
            )
        else:
            if not adapter_result.submission_state_known or not adapter_result.cost_state_known:
                invocation = self._failed(
                    run_id,
                    task_id,
                    tool_id,
                    idempotency_key,
                    started,
                    manifest,
                    params_hash,
                    "submission or cost state is unknown",
                    FailureClass.SUBMISSION_UNKNOWN,
                )
            else:
                self._validate_payload(
                    adapter_result.result, contract.output_required, contract.output_properties, "output"
                )
                invocation = ToolInvocation(
                    uuid4(),
                    run_id,
                    task_id,
                    tool_id,
                    idempotency_key,
                    ToolInvocationStatus.SUCCEEDED,
                    started,
                    datetime.now(UTC),
                    self._manifest_sha(manifest),
                    params_hash,
                    dict(adapter_result.result),
                    adapter_result.evidence,
                )
        self._records[key] = invocation
        return invocation

    def _validate_dispatch(
        self,
        contract: ToolContract,
        task_tools: tuple[str, ...],
        task_timeout: int,
        task_budget: int | float,
        manifest: object,
        tool_id: str,
    ) -> None:
        if not getattr(manifest, "frozen", False):
            raise ToolGatewayError("RunManifest is not frozen")
        tool_versions = getattr(manifest, "tool_versions", {})
        permissions = getattr(manifest, "permissions", ())
        run_timeout = getattr(manifest, "timeout_seconds", 0)
        if tool_id not in task_tools or tool_id not in tool_versions:
            raise ToolGatewayError("Tool is not allowlisted by the Task and frozen RunManifest")
        if str(tool_versions[tool_id]) != contract.version:
            raise ToolGatewayError("Tool Contract version differs from frozen RunManifest")
        if contract.permission not in permissions:
            raise ToolGatewayError("Tool permission is absent from frozen RunManifest")
        if not contract.read_only or contract.max_cost != 0:
            raise ToolGatewayError("V0.1 only permits zero-cost read-only Tool Contracts")
        if task_timeout > run_timeout or task_timeout > contract.timeout_seconds:
            raise ToolGatewayError("Tool timeout exceeds frozen task or Run limit")
        if task_budget < contract.max_cost:
            raise ToolGatewayError("Tool budget is insufficient")

    def _failed(
        self,
        run_id: UUID,
        task_id: UUID,
        tool_id: str,
        key: str,
        started: datetime,
        manifest: object,
        parameters_sha256: str,
        reason: str,
        failure: FailureClass,
    ) -> ToolInvocation:
        status = (
            ToolInvocationStatus.NEEDS_ATTENTION
            if failure is FailureClass.SUBMISSION_UNKNOWN
            else ToolInvocationStatus.REJECTED
        )
        return ToolInvocation(
            uuid4(),
            run_id,
            task_id,
            tool_id,
            key,
            status,
            started,
            datetime.now(UTC),
            self._manifest_sha(manifest),
            parameters_sha256,
            {},
            None,
            failure,
            reason,
        )

    @staticmethod
    def _validate_payload(
        payload: Mapping[str, object],
        required: tuple[str, ...],
        properties: Mapping[str, Mapping[str, object]],
        label: str,
    ) -> None:
        if set(payload) - set(properties) or any(field not in payload for field in required):
            raise ToolGatewayError(f"Tool {label} does not match its versioned contract")
        for name, value in payload.items():
            schema = properties[name]
            expected = schema.get("type")
            if expected == "string" and not isinstance(value, str):
                raise ToolGatewayError(f"Tool {label} field {name} must be a string")
            if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                raise ToolGatewayError(f"Tool {label} field {name} must be an integer")
            values = schema.get("enum")
            if isinstance(values, list) and value not in values:
                raise ToolGatewayError(f"Tool {label} field {name} is outside its closed enum")

    @staticmethod
    def _manifest_sha(manifest: object) -> str:
        value = getattr(manifest, "run_manifest_sha256", "")
        if not isinstance(value, str) or len(value) != 64:
            raise ToolGatewayError("frozen RunManifest hash is required")
        return value


__all__ = [
    "AdapterResult",
    "ToolAdapter",
    "ToolContract",
    "ToolContractRegistry",
    "ToolGateway",
    "ToolGatewayError",
    "ToolInvocation",
    "ToolInvocationStatus",
]
