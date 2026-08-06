from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

import launchscope_api.mcp as mcp_module
from launchscope_api.mcp import app


class FakeMcpApplication:
    def context_get(self, actor, run_id, task_id):
        return {"run_id": str(run_id), "task_id": str(task_id), "product_profile": {}, "evidence_refs": []}


def _headers(token: str = "consumer-token") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-LaunchScope-Tenant-Id": str(uuid4()),
        "X-LaunchScope-Actor-Id": "agentteams-bridge",
        "X-LaunchScope-Run-Id": str(uuid4()),
        "X-LaunchScope-Task-Id": str(uuid4()),
    }


def test_mcp_requires_consumer_credential_and_exposes_only_named_capability(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CONSUMER_TOKEN", "consumer-token")
    monkeypatch.setattr(mcp_module, "application", lambda: FakeMcpApplication())
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    with TestClient(app) as client:
        assert client.post("/mcp/context/", headers=_headers("wrong"), json=body).status_code == 401
        response = client.post("/mcp/context/", headers=_headers(), json=body)
        assert response.status_code == 200
        assert response.json()["result"]["tools"][0]["name"] == "launchscope-context.get.v1"
        assert client.post("/mcp/unknown/", headers=_headers(), json=body).status_code == 404
        response = client.post(
            "/mcp/context/",
            headers=_headers(),
            json={
                "jsonrpc": "2.0", "id": "context", "method": "tools/call",
                "params": {"name": "launchscope-context.get.v1", "arguments": {}},
            },
        )
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["isError"] is False
        assert "token" not in result["structuredContent"]
