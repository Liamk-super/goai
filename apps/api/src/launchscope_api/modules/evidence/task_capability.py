"""Short-lived, read-only task capabilities for Demo MCP routing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TaskCapability:
    tenant_id: UUID
    run_id: UUID
    task_id: UUID
    agent_code: str
    expires_at: int


def _secret() -> bytes:
    value = os.getenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET") or os.getenv("LAUNCHSCOPE_MCP_CONSUMER_TOKEN") or ""
    if len(value) < 16:
        raise ValueError("LAUNCHSCOPE_MCP_CAPABILITY_SECRET must contain at least 16 characters")
    return value.encode("utf-8")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_task_capability(
    tenant_id: UUID, run_id: UUID, task_id: UUID, agent_code: str, *, ttl_seconds: int = 7200
) -> str:
    if not 60 <= ttl_seconds <= 86_400 or not agent_code.strip():
        raise ValueError("task capability lifetime or agent identity is invalid")
    payload = json.dumps(
        {
            "v": 1,
            "tenant_id": str(tenant_id),
            "run_id": str(run_id),
            "task_id": str(task_id),
            "agent_code": agent_code,
            "expires_at": int(time.time()) + ttl_seconds,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return f"{_encode(payload)}.{_encode(signature)}"


def verify_task_capability(token: str) -> TaskCapability:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        payload = _decode(encoded_payload)
        signature = _decode(encoded_signature)
    except (ValueError, TypeError) as exc:
        raise ValueError("task capability is malformed") from exc
    expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("task capability signature is invalid")
    try:
        value = json.loads(payload)
        route = TaskCapability(
            tenant_id=UUID(str(value["tenant_id"])),
            run_id=UUID(str(value["run_id"])),
            task_id=UUID(str(value["task_id"])),
            agent_code=str(value["agent_code"]),
            expires_at=int(value["expires_at"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("task capability payload is invalid") from exc
    if not route.agent_code or route.expires_at < int(time.time()):
        raise ValueError("task capability has expired or lacks an agent identity")
    return route


__all__ = ["TaskCapability", "issue_task_capability", "verify_task_capability"]
