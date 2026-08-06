"""Outbox atomicity, idempotency and Inbox-first consumer tests."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from launchscope_api.infrastructure.db.schema import evaluation_run, inbox_message, outbox_message, usage_record
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.infrastructure.messaging.inbox import InboxConsumer
from launchscope_api.infrastructure.messaging.outbox import OutboxRepository
from launchscope_domain.events import EventEnvelope


def _event(scope, key: str, status: str) -> EventEnvelope:
    return EventEnvelope(
        event_type="run.status_changed",
        tenant_id=scope.tenant_id,
        run_id=scope.run_id,
        payload={"status": status},
        correlation_id=uuid4(),
        idempotency_key=key,
    )


def test_state_and_outbox_commit_together_and_replay_is_idempotent(
    database, runtime_engine, tenant_records
) -> None:
    scope = tenant_records["scope"]
    factory = session_factory(runtime_engine)
    event = _event(scope, "run-status-1", "RUNNING")
    with tenant_transaction(factory, scope) as session:
        session.execute(
            update(evaluation_run)
            .where(evaluation_run.c.id == scope.run_id)
            .values(status="RUNNING")
        )
        first = OutboxRepository(session).enqueue(event, aggregate_id=scope.run_id, scope=scope)
        second = OutboxRepository(session).enqueue(event, aggregate_id=scope.run_id, scope=scope)
        assert first.id == second.id

    with tenant_transaction(factory, scope) as session:
        row = session.execute(
            select(evaluation_run.c.status).where(evaluation_run.c.id == scope.run_id)
        ).scalar_one()
        persisted = OutboxRepository(session).get(first.id, scope)
        assert row == "RUNNING"
        assert persisted is not None and persisted.id == first.id

    failed_event = _event(scope, "run-status-rollback", "FAILED")
    with pytest.raises(RuntimeError), tenant_transaction(factory, scope) as session:
        session.execute(
            update(evaluation_run)
            .where(evaluation_run.c.id == scope.run_id)
            .values(status="FAILED")
        )
        OutboxRepository(session).enqueue(failed_event, aggregate_id=scope.run_id, scope=scope)
        raise RuntimeError("force transaction rollback")

    with tenant_transaction(factory, scope) as session:
        assert session.execute(
            select(evaluation_run.c.status).where(evaluation_run.c.id == scope.run_id)
        ).scalar_one() == "RUNNING"
        assert session.execute(
            select(outbox_message.c.id).where(
                outbox_message.c.tenant_id == scope.tenant_id,
                outbox_message.c.idempotency_key == failed_event.idempotency_key,
            )
        ).first() is None


def test_consumer_writes_inbox_before_business_fact_and_duplicate_runs_once(runtime_engine, tenant_records) -> None:
    scope = tenant_records["scope"]
    factory = session_factory(runtime_engine)
    event = _event(scope, "consume-once-1", "SUCCEEDED")
    calls: list[str] = []

    def handler(session, received) -> None:
        calls.append(str(received.event_id))
        session.execute(
            usage_record.insert().values(
                id=uuid4(),
                tenant_id=scope.tenant_id,
                run_id=scope.run_id,
                task_id=None,
                category="test",
                quantity=1,
                cost=0,
                idempotency_key=f"usage:{received.idempotency_key}",
            )
        )

    with factory() as first_session:
        first = InboxConsumer(first_session, "run-consumer").consume_once(event, handler, scope=scope)
    with factory() as second_session:
        second = InboxConsumer(second_session, "run-consumer").consume_once(event, handler, scope=scope)
    assert first.processed and not first.duplicate
    assert not second.processed and second.duplicate
    assert len(calls) == 1

    with tenant_transaction(factory, scope) as session:
        inbox_count = session.execute(
            select(func.count()).select_from(inbox_message).where(
                inbox_message.c.consumer_name == "run-consumer",
                inbox_message.c.dedupe_key == event.idempotency_key,
            )
        ).scalar_one()
        usage_count = session.execute(
            select(func.count()).select_from(usage_record).where(
                usage_record.c.idempotency_key == f"usage:{event.idempotency_key}"
            )
        ).scalar_one()
        assert inbox_count == 1
        assert usage_count == 1
