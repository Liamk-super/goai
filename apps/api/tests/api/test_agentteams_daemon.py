from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from launchscope_api.modules.evaluation.agentteams_daemon import (
    MatrixHumanClient,
    _event,
    _handoff_content,
    _is_transient_receive_error,
)


def test_idle_rocketmq_long_poll_is_treated_as_transient_not_fatal() -> None:
    """An empty queue must not terminate the dispatch bridge."""

    class AioRpcError(Exception):
        pass

    idle = AioRpcError(
        'status = StatusCode.DEADLINE_EXCEEDED details = "Stream removed (Deadline Exceeded)"'
    )
    assert _is_transient_receive_error(idle) is True
    assert _is_transient_receive_error(RuntimeError("no new message in the queue")) is True
    assert _is_transient_receive_error(RuntimeError("UNAVAILABLE: broker restarting")) is True
    assert _is_transient_receive_error(ValueError("manifest hash mismatch")) is False


def test_rocketmq_envelope_round_trips_to_strict_domain_event() -> None:
    tenant_id, run_id, task_id, correlation_id, event_id = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    event = _event({
        "event_type": "evaluation.task.ready.v1", "event_id": str(event_id),
        "tenant_id": str(tenant_id), "run_id": str(run_id), "task_id": str(task_id),
        "correlation_id": str(correlation_id), "causation_id": None,
        "idempotency_key": "dispatch-one", "schema_version": "1.0",
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {"agent_code": "evaluation-manager"},
    })
    assert event.tenant_id == tenant_id and event.run_id == run_id and event.task_id == task_id


def test_matrix_listener_accepts_only_structured_handoff_json() -> None:
    handoff = {
        "schema_version": "1.0", "tenant_id": str(uuid4()), "run_id": str(uuid4()),
        "task_id": str(uuid4()), "agent_code": "product-engineering", "status": "SUCCEEDED",
    }
    assert _handoff_content({"launchscope_handoff": handoff}) == handoff
    assert _handoff_content({"body": "not json"}) is None
    assert _handoff_content({"body": '{"schema_version":"2.0"}'}) is None
    assert _handoff_content({"body": '{"schema_version":"1.0","run_id":"assignment"}'}) is None


def test_matrix_listener_unwraps_json_fence_and_normalizes_completed_alias() -> None:
    handoff = {
        "schema_version": "1.0", "tenant_id": str(uuid4()), "run_id": str(uuid4()),
        "task_id": str(uuid4()), "agent_code": "business-investment", "status": "COMPLETED",
        "risk": "long-form risk narrative",
        "claims": [{"statement": "unsupported fact", "evidence_ids": [], "hypothesis": False}],
    }
    parsed = _handoff_content({"body": f"```json\n{__import__('json').dumps(handoff)}\n```"})
    assert parsed is not None and parsed["status"] == "SUCCEEDED" and parsed["risk"] == "HIGH"
    assert parsed["claims"][0]["hypothesis"] is True
    assert parsed["dimension"] == "BUSINESS_INVESTMENT"


def test_matrix_listener_extracts_one_fenced_handoff_after_agent_commentary() -> None:
    handoff = {
        "schema_version": "1.0", "tenant_id": str(uuid4()), "run_id": str(uuid4()),
        "task_id": str(uuid4()), "agent_code": "user-evidence", "status": "SUCCEEDED",
        "risk": "LOW", "claims": [],
    }
    body = "Read-only review completed.\n\n```json\n" + json.dumps(handoff) + "\n```"
    parsed = _handoff_content({"body": body})
    assert parsed is not None and parsed["task_id"] == handoff["task_id"]
    assert parsed["dimension"] == "USER_USAGE"


def test_matrix_client_creates_an_isolated_room_for_each_task(monkeypatch) -> None:
    tenant_id, run_id, task_id, correlation_id, event_id = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    event = _event({
        "event_type": "evaluation.task.ready.v1", "event_id": str(event_id),
        "tenant_id": str(tenant_id), "run_id": str(run_id), "task_id": str(task_id),
        "correlation_id": str(correlation_id), "causation_id": None,
        "idempotency_key": "dispatch-isolated", "schema_version": "1.0",
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {
            "team_name": "launchscope-potential-review", "agent_code": "evaluation-manager",
            "stage_code": "LEADER_PLAN", "skill_ref": "skill://leader", "context_token": "signed-token",
            "manifest_sha256": "a" * 64, "handoff_schema": {"name": "AgentHandoffV1"},
            "usage_policy": {"provider_usage_required": False},
            "research_policy": {"material_only": True},
        },
    })
    requests = []
    responses = iter(({"room_id": "!task-room:local"}, {"event_id": "$assignment"}))

    @contextmanager
    def fake_urlopen(request, timeout):
        requests.append(request)
        payload = json.dumps(next(responses)).encode()

        class Response:
            def read(self, _limit):
                return payload

        yield Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    room_id, matrix_event_id = MatrixHumanClient(
        "http://matrix.local", "token", {"evaluation-manager": "@leader:local"}
    ).send_assignment(event)

    assert (room_id, matrix_event_id) == ("!task-room:local", "$assignment")
    assert requests[0].full_url.endswith("/_matrix/client/v3/createRoom")
    assert json.loads(requests[0].data)["invite"] == ["@leader:local"]
    assert str(task_id) in json.loads(requests[0].data)["name"]
    assert "%21task-room%3Alocal" in requests[1].full_url
