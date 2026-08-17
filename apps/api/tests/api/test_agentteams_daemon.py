from __future__ import annotations

import io
import json
import urllib.error
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest

from launchscope_api.modules.evaluation.agentteams_daemon import (
    MatrixHumanClient,
    _contract_failure_handoff,
    _contract_rejection_detail,
    _deliver_assignment,
    _delivery_accounting_mode,
    _event,
    _handoff_content,
    _is_transient_receive_error,
)
from launchscope_api.modules.evaluation.agentteams_delivery import AgentWorkerBusy
from launchscope_api.modules.evaluation.execution_control import RunExecutionPausedError


def test_stale_dispatch_is_rejected_before_matrix_or_usage_side_effects() -> None:
    event = _event({
        "event_type": "evaluation.task.ready.v1", "event_id": str(uuid4()),
        "tenant_id": str(uuid4()), "run_id": str(uuid4()), "task_id": str(uuid4()),
        "correlation_id": str(uuid4()), "causation_id": None,
        "idempotency_key": "stale-dispatch", "schema_version": "1.0",
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {"agent_code": "evaluation-manager"},
    })
    session = Mock()
    session.execute.return_value.mappings.return_value.one_or_none.return_value = None
    matrix = Mock()
    usage_reader = Mock()

    assert _deliver_assignment(session, event, matrix, usage_reader) is False
    matrix.send_assignment.assert_not_called()
    usage_reader.snapshot.assert_not_called()


def test_legacy_accounting_assignment_is_rejected_before_matrix_or_usage_side_effects() -> None:
    tenant_id, run_id, task_id = uuid4(), uuid4(), uuid4()
    event = _event({
        "event_type": "evaluation.task.ready.v1", "event_id": str(uuid4()),
        "tenant_id": str(tenant_id), "run_id": str(run_id), "task_id": str(task_id),
        "correlation_id": str(uuid4()), "causation_id": None,
        "idempotency_key": "usage-endpoint-retry", "schema_version": "1.0",
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {
            "agent_code": "evaluation-manager",
            "dispatch_epoch": 2,
            "control_epoch": 3,
        },
    })

    def mapping_result(value):
        result = Mock()
        result.mappings.return_value.one_or_none.return_value = value
        return result

    def scalar_result(value):
        result = Mock()
        result.scalar_one_or_none.return_value = value
        return result

    manifest_result = Mock()
    manifest_result.scalar_one.return_value = {"model_accounting": {"mode": "COPAW_TASK_DELTA"}}
    session = Mock()
    session.execute.side_effect = [
        mapping_result({"id": task_id}),
        mapping_result({"timeout_seconds": 7200, "dispatch_epoch": 2}),
        manifest_result,
        scalar_result(None),
    ]
    matrix = Mock()
    usage_reader = Mock()

    with pytest.raises(RunExecutionPausedError, match="explicit delivery-scoped model restart"):
        _deliver_assignment(session, event, matrix, usage_reader)

    matrix.send_assignment.assert_not_called()
    usage_reader.snapshot.assert_not_called()


def test_explicit_resume_upgrades_a_legacy_manifest_for_one_delivery() -> None:
    assert _delivery_accounting_mode(
        {"model_accounting": {"mode": "COPAW_TASK_DELTA"}},
        resume_authorized_at=datetime.now(UTC),
    ) == "GATEWAY_DELIVERY"


def test_contract_rejection_detail_exposes_bounded_actionable_reason() -> None:
    error = urllib.error.HTTPError(
        "http://local",
        422,
        "invalid",
        {},
        io.BytesIO(json.dumps({"detail": "finding cites evidence outside the SHA-bound handoff refs"}).encode()),
    )

    assert _contract_rejection_detail(error) == "finding cites evidence outside the SHA-bound handoff refs"


def test_contract_failure_handoff_preserves_redispatch_epoch() -> None:
    tenant_id, run_id, task_id = uuid4(), uuid4(), uuid4()
    failure = _contract_failure_handoff(
        {
            "message_type": "ManagerPlanV2",
            "tenant_id": str(tenant_id),
            "run_id": str(run_id),
            "task_id": str(task_id),
            "dispatch_epoch": 7,
            "agent_code": "evaluation-manager",
        },
        "ManagerPlanV2 rejected: score profile does not match evaluation mode",
    )

    assert failure["dispatch_epoch"] == 7
    assert failure["status"] == "BLOCKED"
    assert failure["failure_class"] == "VALIDATION"


def test_contract_failure_handoff_can_preserve_budget_rejection() -> None:
    failure = _contract_failure_handoff(
        {
            "message_type": "ManagerSynthesisV2",
            "tenant_id": str(uuid4()),
            "run_id": str(uuid4()),
            "task_id": str(uuid4()),
            "dispatch_epoch": 2,
            "agent_code": "evaluation-manager",
        },
        "ManagerSynthesisV2 rejected: model token limit reached",
        failure_class="BUDGET",
    )

    assert failure["dispatch_epoch"] == 2
    assert failure["failure_class"] == "BUDGET"


def test_idle_rocketmq_long_poll_is_treated_as_transient_not_fatal() -> None:
    """An empty queue must not terminate the dispatch bridge."""

    class AioRpcError(Exception):
        pass

    idle = AioRpcError(
        'status = StatusCode.DEADLINE_EXCEEDED details = "Stream removed (Deadline Exceeded)"'
    )
    assert _is_transient_receive_error(idle) is True
    assert _is_transient_receive_error(RuntimeError("no new message in the queue")) is True
    assert _is_transient_receive_error(
        RuntimeError("No topic route info in name server for the topic: launchscope-evaluation-events-v1")
    ) is True
    assert _is_transient_receive_error(RuntimeError("UNAVAILABLE: broker restarting")) is True
    assert _is_transient_receive_error(
        RuntimeError(
            "50001, null. NullPointerException. "
            "org.apache.rocketmq.proxy.grpc.v2.consumer.ReceiveMessageActivity.receiveMessage"
        )
    ) is True
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


def test_matrix_listener_accepts_generation_v4_transport_with_outer_routing() -> None:
    transport = {
        "message_type": "ManagerPlanV1",
        "tenant_id": str(uuid4()),
        "run_id": str(uuid4()),
        "task_id": str(uuid4()),
        "agent_code": "evaluation-manager",
        "document": {"schema_version": "1.0", "plan_id": str(uuid4())},
    }

    assert _handoff_content({"body": json.dumps(transport)}) == transport


@pytest.mark.parametrize(
    ("message_type", "payload_key"),
    [
        ("AgentHandoffV4", "document"),
        ("AuditResultV4", "documents"),
        ("ManagerSynthesisV2", "document"),
    ],
)
def test_matrix_listener_accepts_generation_v6_report_transports(message_type: str, payload_key: str) -> None:
    transport = {
        "message_type": message_type,
        "tenant_id": str(uuid4()),
        "run_id": str(uuid4()),
        "task_id": str(uuid4()),
        "agent_code": (
            "user-evidence"
            if message_type == "AgentHandoffV4"
            else "evidence-auditor"
            if message_type == "AuditResultV4"
            else "evaluation-manager"
        ),
        payload_key: [] if payload_key == "documents" else {"schema_version": "2.0"},
    }

    assert _handoff_content({"body": json.dumps(transport)}) == transport


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


def test_matrix_listener_extracts_one_terminal_handoff_after_agent_commentary() -> None:
    handoff = {
        "schema_version": "1.0", "tenant_id": str(uuid4()), "run_id": str(uuid4()),
        "task_id": str(uuid4()), "agent_code": "evaluation-manager", "status": "SUCCEEDED",
        "risk": "MEDIUM", "claims": [],
    }
    body = "The runtime added commentary before the requested JSON response.\n\n" + json.dumps(handoff)
    parsed = _handoff_content({"body": body})
    assert parsed is not None and parsed["task_id"] == handoff["task_id"]


def test_matrix_listener_rejects_multiple_unfenced_json_objects() -> None:
    handoff = {
        "schema_version": "1.0", "tenant_id": str(uuid4()), "run_id": str(uuid4()),
        "task_id": str(uuid4()), "agent_code": "evaluation-manager", "status": "SUCCEEDED",
    }
    assert _handoff_content({"body": "commentary\n" + json.dumps(handoff) + "\n" + json.dumps(handoff)}) is None


def test_matrix_client_reads_bounded_history_larger_than_the_old_64k_limit(monkeypatch) -> None:
    payload = json.dumps({"chunk": [{"content": {"body": "x" * 80_000}}]}).encode()

    @contextmanager
    def fake_urlopen(_request, timeout):
        assert timeout == 20

        class Response:
            def read(self, limit):
                assert limit == 2_000_001
                return payload

        yield Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = MatrixHumanClient("http://matrix.local", "token", {}, {})._get("/history")
    assert len(result["chunk"][0]["content"]["body"]) == 80_000


def test_matrix_client_uses_provisioned_private_room_after_membership_check(monkeypatch) -> None:
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
    responses = iter((
        {"user_id": "@human:local"},
        {"joined": {"@human:local": {}, "@leader:local": {}}},
        {"event_id": "$cleared"},
        {"origin_server_ts": 1000},
        {"chunk": [{
            "sender": "@leader:local",
            "origin_server_ts": 1001,
            "content": {"body": "**History Cleared!**\n\n- Memory is now empty"},
        }]},
        {"event_id": "$assignment"},
    ))

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
        "http://matrix.local",
        "token",
        {"evaluation-manager": "@leader:local"},
        {"evaluation-manager": "!leader-room:local"},
    ).send_assignment(event)

    assert (room_id, matrix_event_id) == ("!leader-room:local", "$assignment")
    assert requests[0].full_url.endswith("/_matrix/client/v3/account/whoami")
    assert requests[1].full_url.endswith("/rooms/%21leader-room%3Alocal/joined_members")
    assert "launchscope-clear-" in requests[2].full_url
    clear_payload = json.loads(requests[2].data)
    assert clear_payload["body"] == "/clear"
    assert clear_payload["launchscope_control"]["command"] == "clear"
    assert requests[3].full_url.endswith("/event/%24cleared")
    assert requests[4].full_url.endswith("/messages?dir=b&limit=8")
    assert "%21leader-room%3Alocal" in requests[5].full_url
    assert requests[5].method == "PUT"
    assignment_payload = json.loads(requests[5].data)
    assert assignment_payload["m.mentions"] == {"user_ids": ["@leader:local"]}
    assert assignment_payload["body"].startswith("@leader:local\n")
    assert all(not request.full_url.endswith("/_matrix/client/v3/createRoom") for request in requests)


def test_matrix_client_does_not_assign_before_session_isolation_ack(monkeypatch) -> None:
    event = _event({
        "event_type": "evaluation.task.ready.v1", "event_id": str(uuid4()),
        "tenant_id": str(uuid4()), "run_id": str(uuid4()), "task_id": str(uuid4()),
        "correlation_id": str(uuid4()), "causation_id": None,
        "idempotency_key": "dispatch-isolation-timeout", "schema_version": "1.0",
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
    responses = iter((
        {"user_id": "@human:local"},
        {"joined": {"@human:local": {}, "@leader:local": {}}},
        {"event_id": "$cleared"},
        {"origin_server_ts": 1000},
        {"chunk": []},
    ))

    @contextmanager
    def fake_urlopen(request, timeout):
        requests.append(request)
        payload = json.dumps(next(responses)).encode()

        class Response:
            def read(self, _limit):
                return payload

        yield Response()

    monotonic_values = iter((0, 0, 21))
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    client = MatrixHumanClient(
        "http://matrix.local",
        "token",
        {"evaluation-manager": "@leader:local"},
        {"evaluation-manager": "!leader-room:local"},
    )

    with pytest.raises(AgentWorkerBusy, match="isolation acknowledgement timed out"):
        client.send_assignment(event)

    assert len(requests) == 5
    assert len([request for request in requests if request.method == "PUT"]) == 1
    assert "launchscope-clear-" in requests[2].full_url


def test_matrix_client_rejects_room_without_exact_human_and_worker_membership(monkeypatch) -> None:
    event = _event({
        "event_type": "evaluation.task.ready.v1", "event_id": str(uuid4()),
        "tenant_id": str(uuid4()), "run_id": str(uuid4()), "task_id": str(uuid4()),
        "correlation_id": str(uuid4()), "causation_id": None,
        "idempotency_key": "dispatch-membership", "schema_version": "1.0",
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
    responses = iter((
        {"user_id": "@human:local"},
        {"joined": {"@human:local": {}, "@unexpected:local": {}}},
    ))

    @contextmanager
    def fake_urlopen(request, timeout):
        requests.append(request)
        payload = json.dumps(next(responses)).encode()

        class Response:
            def read(self, _limit):
                return payload

        yield Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = MatrixHumanClient(
        "http://matrix.local",
        "token",
        {"evaluation-manager": "@leader:local"},
        {"evaluation-manager": "!leader-room:local"},
    )

    with pytest.raises(RuntimeError, match="membership mismatch"):
        client.send_assignment(event)

    assert len(requests) == 2
    assert all(request.method == "GET" for request in requests)


def test_matrix_client_sends_stop_control_command_to_expired_task_room(monkeypatch) -> None:
    requests = []

    @contextmanager
    def fake_urlopen(request, timeout):
        requests.append(request)

        class Response:
            def read(self, _limit):
                return b'{"event_id":"$stopped"}'

        yield Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    MatrixHumanClient(
        "http://matrix.local",
        "token",
        {"evaluation-manager": "@leader:local"},
        {"evaluation-manager": "!task-room:local"},
    ).stop_task(
        "!task-room:local", "delivery-1", "task-1", 7
    )

    assert "%21task-room%3Alocal" in requests[0].full_url
    assert "launchscope-stop-delivery-1" in requests[0].full_url
    payload = json.loads(requests[0].data)
    assert payload["msgtype"] == "m.text" and payload["body"] == "/stop"
    assert payload["m.mentions"] == {"user_ids": ["@leader:local"]}
    assert payload["launchscope_control"] == {
        "command": "stop",
        "session_id": "matrix:!task-room:local",
        "delivery_id": "delivery-1",
        "task_id": "task-1",
        "dispatch_epoch": 7,
    }


def test_handoff_content_normalizes_leader_runtime_blocker() -> None:
    blocker = {
        "message_type": "LeaderBlockerV1",
        "tenant_id": str(uuid4()),
        "run_id": str(uuid4()),
        "task_id": str(uuid4()),
        "agent_code": "evaluation-manager",
        "status": "BLOCKED",
        "error": {"code": "MCP_CONTEXT_UNREACHABLE", "detail": "connection refused"},
    }

    handoff = _handoff_content({"body": json.dumps(blocker)})

    assert handoff is not None
    assert handoff["schema_version"] == "1.0"
    assert handoff["status"] == "BLOCKED"
    assert handoff["failure_class"] == "RUNTIME_UNAVAILABLE"
    assert handoff["next_action"] == "MCP_CONTEXT_UNREACHABLE: connection refused"
