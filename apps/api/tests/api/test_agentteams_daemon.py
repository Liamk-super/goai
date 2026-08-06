from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from launchscope_api.modules.evaluation.agentteams_daemon import _event, _handoff_content


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
