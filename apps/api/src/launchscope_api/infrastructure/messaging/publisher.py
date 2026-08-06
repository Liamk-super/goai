"""Publish committed Outbox messages and require a transport ACK before PUBLISHED."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_domain.value_objects import TenantScope

from ..db.schema import evaluation_run, run_status_history
from ..db.session import tenant_transaction
from .outbox import OutboxRecord, OutboxRepository


class MessageTransport(Protocol):
    def publish(self, topic: str, body: bytes, *, key: str, tag: str) -> str: ...


class DefinitivePublishFailure(RuntimeError):
    """Transport proves that no Broker submission occurred; bounded retry is safe."""


@dataclass(frozen=True, slots=True)
class PublishBatchResult:
    published: int
    failed: int


class OutboxPublisher:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        transport: MessageTransport,
        *,
        publisher_id: str,
        topic: str = "launchscope-evaluation-events-v1",
    ) -> None:
        self._sessions = sessions
        self._transport = transport
        self._publisher_id = publisher_id
        self._topic = topic

    def publish_scope(self, scope: TenantScope, *, limit: int = 50) -> PublishBatchResult:
        with tenant_transaction(self._sessions, scope) as session:
            claimed = OutboxRepository(session).claim_committed(
                scope, publisher_id=self._publisher_id, limit=limit
            )
        published = failed = 0
        for record in claimed:
            try:
                self._publish(record)
            except DefinitivePublishFailure as exc:
                with tenant_transaction(self._sessions, scope) as session:
                    OutboxRepository(session).mark_failed(record.id, scope, type(exc).__name__)
                failed += 1
            except Exception as exc:
                self._freeze_unknown(record, scope, type(exc).__name__)
                failed += 1
            else:
                with tenant_transaction(self._sessions, scope) as session:
                    OutboxRepository(session).mark_published(
                        record.id, scope, publisher_id=self._publisher_id
                    )
                published += 1
        return PublishBatchResult(published, failed)

    def _freeze_unknown(self, record: OutboxRecord, scope: TenantScope, reason: str) -> None:
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, scope, actor_id=self._publisher_id) as session:
            OutboxRepository(session).mark_submission_unknown(record.id, scope, reason)
            old = session.execute(select(evaluation_run.c.status).where(
                evaluation_run.c.tenant_id == scope.tenant_id,
                evaluation_run.c.id == record.aggregate_id,
            )).scalar_one_or_none()
            if old is not None:
                session.execute(update(evaluation_run).where(
                    evaluation_run.c.tenant_id == scope.tenant_id,
                    evaluation_run.c.id == record.aggregate_id,
                ).values(
                    status="NEEDS_ATTENTION", last_failure_class="SUBMISSION_UNKNOWN",
                    attention_reason="RocketMQ Broker ACK state is unknown; automatic retry prohibited",
                    updated_at=now,
                ))
                session.execute(run_status_history.insert().values(
                    id=uuid4(), tenant_id=scope.tenant_id, run_id=record.aggregate_id,
                    from_status=old, to_status="NEEDS_ATTENTION",
                    reason="RocketMQ Broker ACK state is unknown; automatic retry prohibited",
                    failure_class="SUBMISSION_UNKNOWN", occurred_at=now,
                ))

    def _publish(self, record: OutboxRecord) -> None:
        body = json.dumps(record.payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ack_id = self._transport.publish(
            self._topic,
            body,
            key=str(record.event_id),
            tag=record.event_type,
        )
        if not ack_id:
            raise RuntimeError("RocketMQ publish returned no Broker ACK message id")


class RocketMQTransport:
    """Thin official rocketmq-python-client 5.1.1 adapter for a 5.x Proxy."""

    def __init__(self, endpoints: str, topics: tuple[str, ...]) -> None:
        from rocketmq import ClientConfiguration, Credentials, Producer  # type: ignore[import-untyped]

        self._producer = Producer(ClientConfiguration(endpoints, Credentials()), topics)
        self._producer.startup()

    def publish(self, topic: str, body: bytes, *, key: str, tag: str) -> str:
        from rocketmq import Message

        message = Message()
        message.topic = topic
        message.body = body
        message.keys = key
        message.tag = tag
        receipt = self._producer.send(message)
        return str(getattr(receipt, "message_id", ""))

    def close(self) -> None:
        self._producer.shutdown()


__all__ = [
    "DefinitivePublishFailure", "MessageTransport", "OutboxPublisher", "PublishBatchResult", "RocketMQTransport",
]
