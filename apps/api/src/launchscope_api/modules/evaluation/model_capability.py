"""Opaque delivery-scoped capabilities for the internal model egress gateway."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class DeliveryCapability:
    token: str
    sha256: str
    expires_at: datetime


def delivery_scoped_tokens_enabled() -> bool:
    return os.getenv("DELIVERY_SCOPED_MODEL_TOKEN_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def model_usage_ledger_mode() -> str:
    value = os.getenv("MODEL_USAGE_LEDGER_MODE", "GATEWAY_DELIVERY").strip().upper()
    if value != "GATEWAY_DELIVERY":
        raise RuntimeError("MODEL_USAGE_LEDGER_MODE must be GATEWAY_DELIVERY")
    if not delivery_scoped_tokens_enabled():
        raise RuntimeError("GATEWAY_DELIVERY accounting requires delivery-scoped model credentials")
    return value


def configured_model_ids() -> list[str]:
    roles = (
        "EVALUATION_MANAGER",
        "USER_EVIDENCE",
        "PRODUCT_ENGINEERING",
        "BUSINESS_INVESTMENT",
        "EVIDENCE_AUDITOR",
    )
    fallback = os.getenv("AGENTTEAMS_MODEL_ID", "qwen3.8-max").strip()
    values = {
        os.getenv(f"AGENTTEAMS_MODEL_{role}", "").strip() or fallback
        for role in roles
    }
    return sorted(value for value in values if value)


def issue_delivery_capability(*, ttl_seconds: int | None = None) -> DeliveryCapability:
    ttl = ttl_seconds if ttl_seconds is not None else int(
        os.getenv("LAUNCHSCOPE_DELIVERY_TOKEN_TTL_SECONDS", "3600")
    )
    if not 300 <= ttl <= 86_400:
        raise ValueError("delivery model credential lifetime must be between 5 minutes and 24 hours")
    token = f"lsmg.v2.{base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode('ascii')}"
    return DeliveryCapability(
        token=token,
        sha256=delivery_token_digest(token),
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
    )


def delivery_token_digest(token: str) -> str:
    if not token.startswith("lsmg.v2."):
        raise ValueError("delivery model credential is malformed")
    encoded = token.removeprefix("lsmg.v2.")
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except ValueError as exc:
        raise ValueError("delivery model credential is malformed") from exc
    if len(raw) != 32:
        raise ValueError("delivery model credential must contain 256 bits of entropy")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


__all__ = [
    "DeliveryCapability",
    "configured_model_ids",
    "delivery_scoped_tokens_enabled",
    "delivery_token_digest",
    "issue_delivery_capability",
    "model_usage_ledger_mode",
]
