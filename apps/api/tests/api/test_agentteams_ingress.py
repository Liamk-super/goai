from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from launchscope_api.main import ControlPlane, create_app
from launchscope_api.modules.evaluation import agentteams_daemon
from launchscope_api.modules.evaluation.handoff_application import HandoffResult
from launchscope_orchestrator.agentteams_bridge import SupersededHandoffError


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


class _SupersededHandoffs:
    def consume(self, actor, body, *, run_id, task_id):
        raise SupersededHandoffError(
            "Matrix handoff dispatch_epoch is stale; it answers a superseded dispatch of this Task"
        )


def test_a_superseded_handoff_is_acknowledged_instead_of_rejected(monkeypatch) -> None:
    """A stale reply must not be answered with 400.

    The listener turns 400/422 into a synthetic BLOCKED/VALIDATION handoff and
    retries the same Matrix event forever, which both blames the Agent for a
    benign race and stops the cursor from ever reaching the current-epoch reply.
    """
    monkeypatch.setenv("LAUNCHSCOPE_AGENTTEAMS_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("LAUNCHSCOPE_AGENTTEAMS_BRIDGE_TOKEN", "bridge-test-token")
    app = create_app(ControlPlane.create())
    app.state.handoff_application = _SupersededHandoffs()
    response = TestClient(app).post(
        "/api/v1/internal/agentteams/matrix-events",
        json={"event_id": "$stale"},
        headers={
            "Authorization": "Bearer bridge-test-token", "X-LaunchScope-Tenant-Id": str(uuid4()),
            "X-LaunchScope-Run-Id": str(uuid4()), "X-LaunchScope-Task-Id": str(uuid4()),
        },
    )
    assert response.status_code == 202
    assert response.status_code not in {400, 422}
    body = response.json()
    assert body["task_status"] == "SUPERSEDED"
    assert body["duplicate"] is True
    assert body["report_id"] is None
    assert "stale" in body["discarded_reason"]


def test_the_listener_only_synthesizes_a_failure_for_contract_violations() -> None:
    """The synthetic BLOCKED handoff must stay gated on 400/422 alone.

    Guards the other half of the poison loop: if the daemon ever widened this to
    any HTTPError, the 202 fix above would be silently defeated.
    """
    source = Path(agentteams_daemon.__file__).read_text(encoding="utf-8")
    assert "if exc.code not in {400, 422}:" in source
    assert '"failure_class": "VALIDATION"' in source
    assert 'b\'"SUPERSEDED"\' in payload' in source


def test_listener_canonicalizes_routing_uuids_for_synthetic_validation_failure() -> None:
    tenant_id, run_id, task_id = uuid4(), uuid4(), uuid4()
    noncanonical_task_id = str(task_id).replace("-", "", 1)

    routes = agentteams_daemon._canonical_handoff_routes(
        {
            "tenant_id": str(tenant_id),
            "run_id": str(run_id),
            "task_id": noncanonical_task_id,
        },
        {},
    )

    assert routes == (str(tenant_id), str(run_id), str(task_id))
