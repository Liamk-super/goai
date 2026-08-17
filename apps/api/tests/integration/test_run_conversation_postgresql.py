from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from launchscope_api.infrastructure.db.schema import (
    evaluation_run,
    requirement_brief,
    requirement_change,
    run_conversation_message,
    stage,
    task,
)
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_api.modules.supervisor.conversation_application import RunConversationApplication
from launchscope_api.modules.supervisor.intake_application import SupervisorChatApplication
from launchscope_api.modules.user_validation.application import IdempotencyConflictError


class _Objects:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def put_private(self, key: str, body: bytes, _content_type: str) -> str:
        self.values[key] = body
        return hashlib.sha256(body).hexdigest()

    def get_private(self, key: str, *, max_bytes: int = 2_000_000) -> bytes:
        body = self.values[key]
        if len(body) > max_bytes:
            raise ValueError("object exceeds test read limit")
        return body


def _task_values(records: dict[str, object], stage_id, agent: str, now: datetime) -> dict[str, object]:
    task_id = uuid4()
    return {
        "id": task_id,
        "tenant_id": records["tenant_id"],
        "run_id": records["run_id"],
        "stage_id": stage_id,
        "agent_identity_id": None,
        "skill_version_id": None,
        "stage_code": "DOMAIN_REVIEW",
        "agent_identity_ref": f"{agent}@4.0",
        "skill_ref": f"launchscope-{agent}",
        "skill_version": "4.0",
        "status": "READY",
        "lease_token": None,
        "idempotency_key": f"conversation-{task_id}",
        "dependencies": [],
        "tool_allowlist": [],
        "budget_slice": {"suggested_usd": 0},
        "timeout_seconds": 600,
        "success_condition": ["structured handoff"],
        "evidence_requirement": f"Review {agent} evidence",
        "required": True,
        "correction_attempts": 0,
        "transient_retries": 0,
        "dispatch_epoch": 0,
        "last_failure_class": None,
        "last_error": None,
        "side_effect_started": False,
        "created_at": now,
        "updated_at": now,
    }


def test_specialist_conversation_is_append_only_tenant_scoped_and_routes_only_matching_tasks(
    database, runtime_engine, tenant_records
) -> None:
    now = datetime.now(UTC)
    stage_id, brief_id = uuid4(), uuid4()
    product_task = _task_values(tenant_records, stage_id, "product-engineering", now)
    business_task = _task_values(tenant_records, stage_id, "business-investment", now)
    with database.begin() as connection:
        connection.execute(
            update(evaluation_run)
            .where(evaluation_run.c.id == tenant_records["run_id"])
            .values(status="RUNNING", state_flags={"architecture_generation": "supervisor-1p4-v1"})
        )
        connection.execute(
            requirement_brief.insert().values(
                id=brief_id,
                tenant_id=tenant_records["tenant_id"],
                product_version_id=tenant_records["version_id"],
                revision=1,
                schema_version="1.0",
                raw_input_object_key=f"test/{brief_id}.txt",
                raw_input_sha256="a" * 64,
                document={"schema_version": "1.0"},
                confirmation_required=False,
                status="READY_FOR_PLANNING",
                created_by="integration",
                created_at=now,
                confirmed_at=now,
            )
        )
        connection.execute(
            stage.insert().values(
                id=stage_id,
                tenant_id=tenant_records["tenant_id"],
                run_id=tenant_records["run_id"],
                code="DOMAIN_REVIEW",
                ordinal=2,
                status="RUNNING",
                started_at=now,
                completed_at=None,
            )
        )
        connection.execute(task.insert(), [product_task, business_task])

    objects = _Objects()
    sessions = session_factory(runtime_engine)
    application = RunConversationApplication(
        sessions,
        objects,
        object.__new__(SupervisorChatApplication),
    )
    actor = Actor(tenant_records["tenant_id"], "conversation-integration")
    first = application.submit(
        actor,
        tenant_records["run_id"],
        "product-engineering",
        message="补充：已有可运行原型和部署记录。",
        allow_external_processing=False,
        idempotency_key="conversation-product-1",
        correlation_id=uuid4(),
    )
    replay = application.submit(
        actor,
        tenant_records["run_id"],
        "product-engineering",
        message="补充：已有可运行原型和部署记录。",
        allow_external_processing=False,
        idempotency_key="conversation-product-1",
        correlation_id=uuid4(),
    )

    assert first.route_state == "ROUTED"
    assert first.affected_task_ids == (product_task["id"],)
    assert business_task["id"] not in first.affected_task_ids
    assert replay.duplicate is True and replay.message_id == first.message_id
    projection = application.list_conversations(actor, tenant_records["run_id"])
    assert [item["channel"] for item in projection["channels"]] == [
        "supervisor",
        "user-evidence",
        "product-engineering",
        "business-investment",
    ]
    assert projection["messages"][0]["text"] == "补充：已有可运行原型和部署记录。"
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        stored = session.execute(select(run_conversation_message)).mappings().one()
        change = session.execute(select(requirement_change)).mappings().one()
    assert stored["sha256"] == hashlib.sha256(objects.values[stored["object_key"]]).hexdigest()
    assert change["document"]["target_agent"] == "product-engineering"
    assert change["document"]["affected_task_ids"] == [str(product_task["id"])]

    with pytest.raises(IdempotencyConflictError):
        application.submit(
            actor,
            tenant_records["run_id"],
            "product-engineering",
            message="不同内容",
            allow_external_processing=False,
            idempotency_key="conversation-product-1",
            correlation_id=uuid4(),
        )
    with pytest.raises(NotFoundError):
        application.list_conversations(Actor(uuid4(), "other-tenant"), tenant_records["run_id"])
