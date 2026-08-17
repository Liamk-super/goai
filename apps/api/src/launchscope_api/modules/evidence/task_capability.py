"""Short-lived, exact-tool task capabilities for Demo MCP routing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from uuid import UUID

_AGENT_CODES = (
    "evaluation-manager",
    "business-investment",
    "product-engineering",
    "user-evidence",
    "evidence-auditor",
)
_TOOL_CODES = (
    "launchscope-context.get.v1",
    "launchscope-context.get.v2",
    "material.read.v1",
    "public-research-search.v1",
    "browser-audit.v1",
    "repository.read.v1",
    "user-validation-audit-context.get.v1",
)


@dataclass(frozen=True, slots=True)
class TaskCapability:
    tenant_id: UUID
    run_id: UUID
    task_id: UUID
    agent_code: str
    allowed_tools: tuple[str, ...]
    expires_at: int
    control_epoch: int = 0


def _secret() -> bytes:
    value = os.getenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET") or os.getenv("LAUNCHSCOPE_MCP_CONSUMER_TOKEN") or ""
    if len(value) < 16:
        raise ValueError("LAUNCHSCOPE_MCP_CAPABILITY_SECRET must contain at least 16 characters")
    return value.encode("utf-8")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if _encode(decoded) != value:
        raise ValueError("task capability encoding is not canonical")
    return decoded


def issue_task_capability(
    tenant_id: UUID,
    run_id: UUID,
    task_id: UUID,
    agent_code: str,
    *,
    allowed_tools: tuple[str, ...] = (),
    ttl_seconds: int = 7200,
    control_epoch: int = 0,
) -> str:
    if not 60 <= ttl_seconds <= 86_400 or not agent_code.strip():
        raise ValueError("task capability lifetime or agent identity is invalid")
    canonical_tools = tuple(sorted(set(allowed_tools)))
    if control_epoch < 0:
        raise ValueError("task capability control epoch is invalid")
    if agent_code in _AGENT_CODES and all(tool in _TOOL_CODES for tool in canonical_tools):
        tools_mask = sum(1 << _TOOL_CODES.index(tool) for tool in canonical_tools)
        expires_at = int(time.time()) + ttl_seconds
        payload = b"".join(
            (
                b"\x04",
                tenant_id.bytes,
                run_id.bytes,
                task_id.bytes,
                expires_at.to_bytes(8, "big"),
                bytes((_AGENT_CODES.index(agent_code), tools_mask)),
                control_epoch.to_bytes(4, "big"),
            )
        )
        signature = hmac.new(_secret(), payload, hashlib.sha256).digest()
        return f"h4.{payload.hex()}.{signature.hex()}"
    payload = json.dumps(
        {
            "v": 1,
            "tenant_id": str(tenant_id),
            "run_id": str(run_id),
            "task_id": str(task_id),
            "agent_code": agent_code,
            "allowed_tools": canonical_tools,
            "expires_at": int(time.time()) + ttl_seconds,
            "control_epoch": control_epoch,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return f"h2.{payload.hex()}.{signature.hex()}"


def verify_task_capability(token: str) -> TaskCapability:
    try:
        parts = token.split(".")
        if len(parts) == 3 and parts[0] in {"h2", "h3", "h4"}:
            if parts[1] != parts[1].lower() or parts[2] != parts[2].lower():
                raise ValueError("task capability hex encoding is not canonical")
            encoded_payload = parts[1].replace("-", "") if parts[0] in {"h3", "h4"} else parts[1]
            payload = bytes.fromhex(encoded_payload)
            signature = bytes.fromhex(parts[2])
        elif len(parts) == 2:
            payload = _decode(parts[0])
            signature = _decode(parts[1])
        else:
            raise ValueError("task capability segment count is invalid")
    except (ValueError, TypeError) as exc:
        raise ValueError("task capability is malformed") from exc
    expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("task capability signature is invalid")
    if parts[0] in {"h3", "h4"}:
        expected_length = 63 if parts[0] == "h4" else 59
        expected_version = 4 if parts[0] == "h4" else 3
        if len(payload) != expected_length or payload[0] != expected_version:
            raise ValueError("task capability payload is invalid")
        agent_index = payload[57]
        tools_mask = payload[58]
        if agent_index >= len(_AGENT_CODES) or tools_mask >> len(_TOOL_CODES):
            raise ValueError("task capability payload is invalid")
        route = TaskCapability(
            tenant_id=UUID(bytes=payload[1:17]),
            run_id=UUID(bytes=payload[17:33]),
            task_id=UUID(bytes=payload[33:49]),
            agent_code=_AGENT_CODES[agent_index],
            allowed_tools=tuple(tool for index, tool in enumerate(_TOOL_CODES) if tools_mask & (1 << index)),
            expires_at=int.from_bytes(payload[49:57], "big"),
            control_epoch=int.from_bytes(payload[59:63], "big") if parts[0] == "h4" else 0,
        )
        if route.expires_at < int(time.time()):
            raise ValueError("task capability has expired or lacks an agent identity")
        return route
    try:
        value = json.loads(payload)
        route = TaskCapability(
            tenant_id=UUID(str(value["tenant_id"])),
            run_id=UUID(str(value["run_id"])),
            task_id=UUID(str(value["task_id"])),
            agent_code=str(value["agent_code"]),
            allowed_tools=tuple(str(item) for item in value.get("allowed_tools", [])),
            expires_at=int(value["expires_at"]),
            control_epoch=int(value.get("control_epoch", 0)),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("task capability payload is invalid") from exc
    if not route.agent_code or route.expires_at < int(time.time()):
        raise ValueError("task capability has expired or lacks an agent identity")
    return route


__all__ = ["TaskCapability", "issue_task_capability", "verify_task_capability"]
