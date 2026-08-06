from __future__ import annotations

from fastapi.testclient import TestClient

from launchscope_api.main import ControlPlane, create_app

LOCAL_ORIGIN = "http://127.0.0.1:3000"


def test_demo_routes_are_not_registered_when_demo_mode_is_disabled(monkeypatch) -> None:
    monkeypatch.delenv("LAUNCHSCOPE_DEMO_MODE", raising=False)
    client = TestClient(create_app(ControlPlane.create()))
    response = client.post(
        "/api/v1/demo/sessions",
        headers={"Origin": LOCAL_ORIGIN},
        json={"display_name": "Ada"},
    )
    assert response.status_code == 404


def test_demo_session_creates_and_validates_isolated_workspace(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_DEMO_MODE", "true")
    client = TestClient(create_app(ControlPlane.create()))
    created = client.post(
        "/api/v1/demo/sessions",
        headers={"Origin": LOCAL_ORIGIN},
        json={"display_name": "  Ada  "},
    )
    assert created.status_code == 201, created.text
    session = created.json()
    assert session["schemaVersion"] == "launchscope.demo.session.v1"
    assert session["displayName"] == "Ada"
    assert "password" not in session and "token" not in session

    validated = client.get(
        "/api/v1/demo/session",
        headers={
            "Origin": LOCAL_ORIGIN,
            "X-Tenant-Id": session["tenantId"],
            "X-Actor-Id": session["actorId"],
            "X-Workspace-Id": session["workspaceId"],
        },
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["valid"] is True


def test_demo_session_rejects_nonlocal_origin_invalid_name_and_cross_workspace(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_DEMO_MODE", "true")
    client = TestClient(create_app(ControlPlane.create()))
    assert client.post(
        "/api/v1/demo/sessions", headers={"Origin": "https://example.com"}, json={"display_name": "Ada"}
    ).status_code == 403
    assert client.post(
        "/api/v1/demo/sessions", headers={"Origin": LOCAL_ORIGIN}, json={"display_name": " "}
    ).status_code == 422

    first = client.post(
        "/api/v1/demo/sessions", headers={"Origin": LOCAL_ORIGIN}, json={"display_name": "Ada"}
    ).json()
    second = client.post(
        "/api/v1/demo/sessions", headers={"Origin": LOCAL_ORIGIN}, json={"display_name": "Grace"}
    ).json()
    response = client.get(
        "/api/v1/demo/session",
        headers={
            "Origin": LOCAL_ORIGIN,
            "X-Tenant-Id": first["tenantId"],
            "X-Actor-Id": first["actorId"],
            "X-Workspace-Id": second["workspaceId"],
        },
    )
    assert response.status_code == 404
