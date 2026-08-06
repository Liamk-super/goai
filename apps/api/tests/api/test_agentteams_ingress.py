from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from launchscope_api.main import ControlPlane, create_app
from launchscope_api.modules.evaluation.handoff_application import HandoffResult


class _Handoffs:
    def consume(self, actor, body, *, run_id, task_id):
        assert actor.actor_id == "agentteams-matrix-bridge"
        assert body["event_id"] == "$one" and run_id and task_id
        return HandoffResult("$one", "SUCCEEDED", "RUNNING")


def test_agentteams_ingress_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("LAUNCHSCOPE_AGENTTEAMS_BRIDGE_ENABLED", raising=False)
    client = TestClient(create_app(ControlPlane.create()))
    assert client.post("/api/v1/internal/agentteams/matrix-events").status_code == 404


def test_agentteams_ingress_requires_service_token(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_AGENTTEAMS_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("LAUNCHSCOPE_AGENTTEAMS_BRIDGE_TOKEN", "bridge-test-token")
    app = create_app(ControlPlane.create())
    app.state.handoff_application = _Handoffs()
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer bridge-test-token", "X-LaunchScope-Tenant-Id": str(uuid4()),
        "X-LaunchScope-Run-Id": str(uuid4()), "X-LaunchScope-Task-Id": str(uuid4()),
    }
    assert client.post(
        "/api/v1/internal/agentteams/matrix-events", json={"event_id": "$one"},
        headers={key: value for key, value in headers.items() if key != "Authorization"},
    ).status_code == 401
    response = client.post(
        "/api/v1/internal/agentteams/matrix-events", json={"event_id": "$one"}, headers=headers
    )
    assert response.status_code == 202 and response.json()["matrix_event_id"] == "$one"
