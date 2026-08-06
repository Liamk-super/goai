from __future__ import annotations

from fastapi.testclient import TestClient

from launchscope_api.main import ControlPlane, create_app


def client_and_plane() -> tuple[TestClient, ControlPlane]:
    plane = ControlPlane.create()
    return TestClient(create_app(plane)), plane


def create_tenant(client: TestClient, actor_id: str, slug: str) -> tuple[str, str]:
    response = client.post(
        "/api/v1/tenants",
        headers={"X-Actor-Id": actor_id},
        json={"slug": slug, "workspace_name": f"{slug} workspace"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return payload["tenant_id"], payload["workspace_id"]


def headers(tenant_id: str, actor_id: str, correlation_id: str) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id, "X-Actor-Id": actor_id, "X-Correlation-Id": correlation_id}
