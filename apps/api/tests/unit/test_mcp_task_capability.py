from __future__ import annotations

from uuid import uuid4

import pytest

from launchscope_api.mcp import _routing
from launchscope_api.modules.evidence.task_capability import issue_task_capability, verify_task_capability


def test_task_capability_binds_tenant_run_task_and_agent(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "demo-secret-with-enough-entropy")
    tenant_id, run_id, task_id = uuid4(), uuid4(), uuid4()
    token = issue_task_capability(tenant_id, run_id, task_id, "product-engineering", ttl_seconds=600)
    route = verify_task_capability(token)
    assert (route.tenant_id, route.run_id, route.task_id, route.agent_code) == (
        tenant_id, run_id, task_id, "product-engineering"
    )


def test_task_capability_rejects_tampering(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "demo-secret-with-enough-entropy")
    token = issue_task_capability(uuid4(), uuid4(), uuid4(), "evaluation-manager", ttl_seconds=600)
    with pytest.raises(ValueError, match="signature"):
        verify_task_capability(token[:-1] + ("A" if token[-1] != "A" else "B"))


def test_demo_mcp_routing_is_derived_from_task_capability(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_DEMO_MODE", "true")
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "demo-secret-with-enough-entropy")
    tenant_id, run_id, task_id = uuid4(), uuid4(), uuid4()
    token = issue_task_capability(tenant_id, run_id, task_id, "user-evidence", ttl_seconds=600)
    route = _routing(token)
    assert (route.tenant_id, route.run_id, route.task_id, route.actor_id) == (
        tenant_id, run_id, task_id, "agent:user-evidence"
    )
