from __future__ import annotations

from uuid import uuid4

import pytest

from launchscope_api.mcp import _routing
from launchscope_api.modules.evidence.task_capability import issue_task_capability, verify_task_capability


def test_task_capability_binds_tenant_run_task_and_agent(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "demo-secret-with-enough-entropy")
    tenant_id, run_id, task_id = uuid4(), uuid4(), uuid4()
    token = issue_task_capability(
        tenant_id, run_id, task_id, "product-engineering", ttl_seconds=600, control_epoch=7
    )
    assert token.startswith("h4.")
    assert len(token) < 220
    assert set(token.removeprefix("h4.").replace(".", "")) <= set("0123456789abcdef")
    assert "mv" not in token.lower()
    route = verify_task_capability(token)
    assert (route.tenant_id, route.run_id, route.task_id, route.agent_code) == (
        tenant_id, run_id, task_id, "product-engineering"
    )
    assert route.control_epoch == 7


def test_task_capability_compact_format_preserves_allowed_tools(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "demo-secret-with-enough-entropy")
    allowed_tools = ("launchscope-context.get.v1", "public-research-search.v1")
    token = issue_task_capability(
        uuid4(), uuid4(), uuid4(), "business-investment", allowed_tools=allowed_tools, ttl_seconds=600
    )
    assert verify_task_capability(token).allowed_tools == allowed_tools


def test_compact_capability_tolerates_uuid_style_payload_hyphens(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "demo-secret-with-enough-entropy")
    token = issue_task_capability(uuid4(), uuid4(), uuid4(), "evidence-auditor", ttl_seconds=600)
    prefix, payload, signature = token.split(".")
    hyphenated = f"{prefix}.{payload[:17]}-{payload[17:49]}-{payload[49:]}.{signature}"

    assert verify_task_capability(hyphenated) == verify_task_capability(token)


def test_task_capability_rejects_tampering(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "demo-secret-with-enough-entropy")
    token = issue_task_capability(uuid4(), uuid4(), uuid4(), "evaluation-manager", ttl_seconds=600)
    # A flipped trailing character is refused either as a bad signature or as a
    # non-canonical encoding; both are rejections, so accept either reason.
    with pytest.raises(ValueError, match="signature|malformed"):
        verify_task_capability(token[:-1] + ("A" if token[-1] != "A" else "B"))


def test_task_capability_rejects_payload_tampering(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "demo-secret-with-enough-entropy")
    token = issue_task_capability(uuid4(), uuid4(), uuid4(), "evaluation-manager", ttl_seconds=600)
    body, _, signature = token.rpartition(".")
    forged = issue_task_capability(uuid4(), uuid4(), uuid4(), "product-engineering", ttl_seconds=600)
    with pytest.raises(ValueError, match="signature"):
        verify_task_capability(f"{forged.rpartition('.')[0]}.{signature}")
    assert verify_task_capability(f"{body}.{signature}").agent_code == "evaluation-manager"


def test_task_capability_rejects_non_canonical_signature_encoding(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "demo-secret-with-enough-entropy")
    token = issue_task_capability(uuid4(), uuid4(), uuid4(), "user-evidence", ttl_seconds=600)
    # Hex capability signatures accept only lowercase canonical characters.
    variants = {token[:-1] + char for char in "ABCDEFGHIJKLMNOP"} - {token}
    rejected = 0
    for variant in variants:
        try:
            verify_task_capability(variant)
        except ValueError:
            rejected += 1
    assert rejected == len(variants)


def test_demo_mcp_routing_is_derived_from_task_capability(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_DEMO_MODE", "true")
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "demo-secret-with-enough-entropy")
    monkeypatch.setattr("launchscope_api.mcp._assert_route_active", lambda *_args, **_kwargs: None)
    tenant_id, run_id, task_id = uuid4(), uuid4(), uuid4()
    token = issue_task_capability(tenant_id, run_id, task_id, "user-evidence", ttl_seconds=600)
    route = _routing(token)
    assert (route.tenant_id, route.run_id, route.task_id, route.actor_id) == (
        tenant_id, run_id, task_id, "agent:user-evidence"
    )
