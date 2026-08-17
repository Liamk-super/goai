from __future__ import annotations

import json

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


def test_default_demo_session_restores_existing_membership_without_creating_identity(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_DEMO_MODE", "true")
    binding = tmp_path / "default-workspace.json"
    monkeypatch.setenv("LAUNCHSCOPE_DEMO_DEFAULT_WORKSPACE_FILE", str(binding))
    control_plane = ControlPlane.create()
    client = TestClient(create_app(control_plane))
    created = client.post(
        "/api/v1/demo/sessions",
        headers={"Origin": LOCAL_ORIGIN},
        json={"display_name": "Existing Demo"},
    ).json()
    binding.write_text(json.dumps(created), encoding="utf-8")
    tenant_count = len(control_plane.identity.store.tenants)
    workspace_count = len(control_plane.identity.store.workspaces)

    restored = client.get("/api/v1/demo/default-session", headers={"Origin": LOCAL_ORIGIN})

    assert restored.status_code == 200, restored.text
    assert {key: value for key, value in restored.json().items() if key != "createdAt"} == {
        key: value for key, value in created.items() if key != "createdAt"
    }
    assert restored.json()["createdAt"].replace("Z", "+00:00") == created["createdAt"]
    assert len(control_plane.identity.store.tenants) == tenant_count
    assert len(control_plane.identity.store.workspaces) == workspace_count


def test_default_demo_session_fails_closed_for_missing_binding_and_nonlocal_origin(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_DEMO_MODE", "true")
    monkeypatch.setenv("LAUNCHSCOPE_DEMO_DEFAULT_WORKSPACE_FILE", str(tmp_path / "missing.json"))
    client = TestClient(create_app(ControlPlane.create()))
    assert client.get("/api/v1/demo/default-session", headers={"Origin": LOCAL_ORIGIN}).status_code == 503
    assert client.get(
        "/api/v1/demo/default-session", headers={"Origin": "https://example.com"}
    ).status_code == 403
