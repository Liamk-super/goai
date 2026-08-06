from __future__ import annotations

import hashlib
from uuid import UUID

import pytest
from sqlalchemy import select, text

from launchscope_api.infrastructure.db.schema import decision, evidence, finding, matrix_event_receipt, report, task
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_api.modules.evaluation.handoff_application import HandoffApplication
from launchscope_api.modules.identity_tenant.application import Actor


class _Directory:
    def agent_for_mxid(self, mxid: str) -> str | None:
        return mxid.removeprefix("@").split(":", 1)[0] if mxid.startswith("@") else None


class _Objects:
    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str:
        assert object_key.endswith(".json") and mime_type == "application/json" and payload
        return hashlib.sha256(payload).hexdigest()


def test_matrix_handoffs_are_idempotent_and_unlock_rule_owned_report(
    database, runtime_engine, tenant_records
) -> None:
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    with database.begin() as connection:
        connection.execute(text("UPDATE evaluation_run SET status='PLANNED' WHERE id=:id"), {"id": run_id})
    actor = Actor(tenant_id, "local-demo:test")
    sessions = session_factory(runtime_engine)
    DispatchApplication(sessions).dispatch(actor, run_id, idempotency_key="handoff-flow")
    application = HandoffApplication(sessions, _Objects(), _Directory())  # type: ignore[arg-type]

    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        evidence_id = session.execute(select(evidence.c.id).where(evidence.c.run_id == run_id)).scalar_one()
        rows = session.execute(select(task.c.id, task.c.agent_identity_ref, task.c.stage_code).where(
            task.c.tenant_id == tenant_id, task.c.run_id == run_id
        ).order_by(task.c.created_at, task.c.id)).all()

    dimensions = {
        "product-engineering": "PRODUCT_IMPLEMENTATION", "user-evidence": "USER_USAGE",
        "business-investment": "BUSINESS_INVESTMENT", "geo-policy-trend": "GEO_POLICY_TREND",
    }
    events: dict[str, dict[str, object]] = {}
    for index, (task_id, identity, stage_code) in enumerate(rows):
        agent = identity.split("@", 1)[0]
        dimension = dimensions.get(agent, "CONTROL")
        claims = [] if agent not in dimensions else [{
            "statement": f"Evidence-backed {dimension} observation", "evidence_ids": [str(evidence_id)],
            "hypothesis": False, "region": "GLOBAL",
            **({"fetched_at": "2026-08-06T00:00:00Z", "valid_until": "2026-11-06T00:00:00Z",
                "trend_signal": "NEUTRAL"} if agent == "geo-policy-trend" else {}),
        }]
        event = {
            "event_id": f"$event-{index}", "room_id": f"!run-{run_id}:local",
            "sender": f"@{agent}:local", "content": {
                "schema_version": "1.0", "tenant_id": str(tenant_id),
                "run_id": str(run_id), "task_id": str(task_id),
                "agent_code": agent, "status": "SUCCEEDED", "dimension": dimension,
                "claims": claims, "evidence_refs": [str(evidence_id)] if claims else [],
                "risk": "LOW", "confidence": 0.7, "needs_human_approval": False,
                "next_action": f"Complete {stage_code}",
                "provider_usage": {
                    "receipt_id": f"provider-{index}", "input_tokens": 100, "output_tokens": 50,
                    "cost_usd": "0.10", "submission_known": True, "usage_known": True,
                },
            },
        }
        events[stage_code + agent] = event

    ordered = [
        next(value for key, value in events.items() if key.startswith("LEADER_PLANNING")),
        *[next(value for key, value in events.items() if key.endswith(agent)) for agent in dimensions],
        next(value for key, value in events.items() if key.startswith("EVIDENCE_AUDIT")),
        next(value for key, value in events.items() if key.startswith("RULE_SYNTHESIS")),
    ]
    task_by_event = {
        event["event_id"]: UUID(str(event["content"]["task_id"])) for event in ordered  # type: ignore[index]
    }
    for event in ordered:
        if event["content"]["agent_code"] == "evidence-auditor":  # type: ignore[index]
            with tenant_transaction(sessions, tenant_records["scope"]) as session:
                finding_ids = session.execute(select(finding.c.id).where(
                    finding.c.tenant_id == tenant_id, finding.c.run_id == run_id
                )).scalars().all()
            event["content"]["audit_results"] = [  # type: ignore[index]
                {"finding_id": str(finding_id), "decision": "ACCEPTED", "reason": "Durable evidence is linked"}
                for finding_id in finding_ids
            ]
        result = application.consume(actor, event, run_id=run_id, task_id=task_by_event[event["event_id"]])
    replay = application.consume(actor, ordered[1], run_id=run_id, task_id=task_by_event[ordered[1]["event_id"]])
    assert replay.duplicate is True
    assert result.run_status == "COMPLETED" and result.report_id is not None

    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        assert session.execute(select(matrix_event_receipt.c.id).where(
            matrix_event_receipt.c.run_id == run_id
        )).all().__len__() == 7
        grades = session.execute(select(decision.c.dimension_grades).where(decision.c.run_id == run_id)).scalar_one()
        assert set(grades) == set(dimensions.values()) and set(grades.values()) == {"MODERATE"}
        actions = session.execute(select(report.c.action_items).where(report.c.run_id == run_id)).scalar_one()
        assert len(actions) <= 3


@pytest.mark.parametrize(
    ("usage", "failure"),
    [
        (None, "SUBMISSION_UNKNOWN"),
        ({
            "receipt_id": "over-budget", "input_tokens": 10, "output_tokens": 10,
            "cost_usd": "20.01", "submission_known": True, "usage_known": True,
        }, "BUDGET"),
    ],
)
def test_missing_or_over_budget_provider_usage_freezes_without_advancing(
    database, runtime_engine, tenant_records, usage, failure
) -> None:
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    with database.begin() as connection:
        connection.execute(text("UPDATE evaluation_run SET status='PLANNED' WHERE id=:id"), {"id": run_id})
    actor = Actor(tenant_id, "local-demo:test")
    sessions = session_factory(runtime_engine)
    DispatchApplication(sessions).dispatch(actor, run_id, idempotency_key=f"usage-{failure}")
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        task_id = session.execute(select(task.c.id).where(
            task.c.run_id == run_id, task.c.stage_code == "LEADER_PLANNING"
        )).scalar_one()
    content = {
        "schema_version": "1.0", "tenant_id": str(tenant_id), "run_id": str(run_id), "task_id": str(task_id),
        "agent_code": "evaluation-manager", "status": "SUCCEEDED", "dimension": "CONTROL",
        "claims": [], "evidence_refs": [], "risk": "LOW", "confidence": 0.7,
        "needs_human_approval": False, "next_action": "Continue",
    }
    if usage is not None:
        content["provider_usage"] = usage
    result = HandoffApplication(
        sessions, _Objects(), _Directory(), require_provider_usage=True  # type: ignore[arg-type]
    ).consume(
        actor,
        {"event_id": f"$usage-{failure}", "room_id": "!run:local", "sender": "@evaluation-manager:local",
         "content": content},
        run_id=run_id, task_id=task_id,
    )
    assert result.run_status == "NEEDS_ATTENTION"
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        row = session.execute(select(task.c.status, task.c.last_failure_class).where(task.c.id == task_id)).one()
        assert row == ("NEEDS_ATTENTION", failure)


def test_demo_can_explicitly_accept_a_handoff_without_provider_usage(
    database, runtime_engine, tenant_records
) -> None:
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    with database.begin() as connection:
        connection.execute(text("UPDATE evaluation_run SET status='PLANNED' WHERE id=:id"), {"id": run_id})
    actor = Actor(tenant_id, "local-demo:test")
    sessions = session_factory(runtime_engine)
    DispatchApplication(sessions).dispatch(actor, run_id, idempotency_key="demo-no-usage")
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        task_id = session.execute(select(task.c.id).where(
            task.c.run_id == run_id, task.c.stage_code == "LEADER_PLANNING"
        )).scalar_one()
    content = {
        "schema_version": "1.0", "tenant_id": str(tenant_id), "run_id": str(run_id),
        "task_id": str(task_id), "agent_code": "evaluation-manager", "status": "SUCCEEDED",
        "dimension": "CONTROL", "claims": [], "evidence_refs": [], "risk": "LOW",
        "confidence": 0.7, "needs_human_approval": False, "next_action": "Delegate domain tasks",
    }
    result = HandoffApplication(
        sessions, _Objects(), _Directory(), require_provider_usage=False  # type: ignore[arg-type]
    ).consume(
        actor, {"event_id": "$demo-no-usage", "room_id": "!run:local",
                "sender": "@evaluation-manager:local", "content": content},
        run_id=run_id, task_id=task_id,
    )
    assert result.task_status == "SUCCEEDED" and result.run_status == "RUNNING"


def test_demo_structured_agent_failure_is_persisted_as_needs_attention(
    database, runtime_engine, tenant_records
) -> None:
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    with database.begin() as connection:
        connection.execute(text("UPDATE evaluation_run SET status='PLANNED' WHERE id=:id"), {"id": run_id})
    actor = Actor(tenant_id, "local-demo:test")
    sessions = session_factory(runtime_engine)
    DispatchApplication(sessions).dispatch(actor, run_id, idempotency_key="demo-structured-failure")
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        task_id = session.execute(select(task.c.id).where(
            task.c.run_id == run_id, task.c.stage_code == "LEADER_PLANNING"
        )).scalar_one()
    content = {
        "schema_version": "1.0", "tenant_id": str(tenant_id), "run_id": str(run_id),
        "task_id": str(task_id), "agent_code": "evaluation-manager", "status": "BLOCKED",
        "dimension": "CONTROL", "claims": [], "evidence_refs": [], "risk": "MEDIUM",
        "confidence": 0.0, "needs_human_approval": True, "failure_class": "VALIDATION",
        "next_action": "Correct the assignment or tool configuration",
    }
    result = HandoffApplication(
        sessions, _Objects(), _Directory(), require_provider_usage=False  # type: ignore[arg-type]
    ).consume(
        actor, {"event_id": "$demo-structured-failure", "room_id": "!run:local",
                "sender": "@evaluation-manager:local", "content": content},
        run_id=run_id, task_id=task_id,
    )
    assert result.task_status == "NEEDS_ATTENTION" and result.run_status == "NEEDS_ATTENTION"
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        assert session.execute(select(task.c.last_failure_class).where(task.c.id == task_id)).scalar_one() == "VALIDATION"
