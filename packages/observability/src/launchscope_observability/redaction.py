"""Fail-closed telemetry redaction shared by API, workers and adapters."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .semconv import ALLOWED_ATTRIBUTES

REDACTED = "[REDACTED]"
SENSITIVE_NAMES = frozenset(
    {
        "authorization",
        "access_token",
        "refresh_token",
        "api_key",
        "secret",
        "password",
        "cookie",
        "set_cookie",
        "prompt",
        "system_prompt",
        "messages",
        "material_body",
        "report_body",
        "evidence_body",
        "private_reasoning",
        "chain_of_thought",
        "thoughts",
    }
)


def payload_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def redact(value: object, *, key: str = "") -> object:
    """Recursively redact named secrets/bodies before structured logging."""

    normalized = key.lower().replace("-", "_")
    if normalized in SENSITIVE_NAMES or any(part in normalized for part in ("password", "secret", "token")):
        return REDACTED
    if isinstance(value, Mapping):
        return {str(child_key): redact(child, key=str(child_key)) for child_key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(child, key=key) for child in value]
    return value


def safe_trace_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Drop unknown fields and redact allowed values as a second guard."""

    return {key: redact(value, key=key) for key, value in attributes.items() if key in ALLOWED_ATTRIBUTES}


__all__ = ["REDACTED", "payload_sha256", "redact", "safe_trace_attributes"]
