"""Inbox-first, same-transaction message consumer."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.orm import Session

from launchscope_domain.events import EventEnvelope
from launchscope_domain.value_objects import TenantScope

from ..db.rls import set_local_tenant_context
from ..db.schema import inbox_message
from .outbox import IdempotencyConflict, _assert_safe_payload

_CONSUMER_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")


@dataclass(frozen=True, slots=True)
class InboxResult:
    message_id: UUID
    processed: bool
    duplicate: bool


def _handler_call(handler: Callable[..., object], session: Session, event: EventEnvelope) -> object:
    """Support both explicit ``handler(session, event)`` and event-only ports."""

    parameters = inspect.signature(handler).parameters
    if len(parameters) >= 2:
        return handler(session, event)
    return handler(event)


class InboxConsumer:
    """Deduplicate before business handling and commit both facts together."""

    def __init__(self, session: Session, consumer_name: str) -> None:
        if _CONSUMER_NAME.fullmatch(consumer_name) is None:
            raise ValueError("consumer_name contains invalid characters")
        self.session = session
        self.consumer_name = consumer_name

    def consume_once(
        self,
        event: EventEnvelope,
        handler: Callable[..., object],
        *,
        scope: TenantScope,
        outbox_message_id: UUID | str | None = None,
    ) -> InboxResult:
        if UUID(str(event.tenant_id)) != scope.tenant_id:
            raise IdempotencyConflict("event tenant does not match consumer tenant")
        payload = event.to_dict()
        _assert_safe_payload(payload)
        started_transaction = not self.session.in_transaction()
        transaction = self.session.begin() if started_transaction else None
        try:
            set_local_tenant_context(self.session.connection(), scope)
            values = {
                "id": uuid4(),
                "tenant_id": scope.tenant_id,
                "outbox_message_id": UUID(str(outbox_message_id)) if outbox_message_id else None,
                "consumer_name": self.consumer_name,
                "dedupe_key": event.idempotency_key,
                "event_id": event.event_id,
                "event_type": event.event_type,
                "payload": payload,
                "processing_status": "PROCESSING",
                "received_at": datetime.now(UTC),
                "created_at": datetime.now(UTC),
            }
            if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
                self.session.execute(
                    postgres_insert(inbox_message)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=["tenant_id", "consumer_name", "dedupe_key"])
                )
            else:
                existing = self.session.execute(
                    select(inbox_message.c.id).where(
                        inbox_message.c.tenant_id == scope.tenant_id,
                        inbox_message.c.consumer_name == self.consumer_name,
                        inbox_message.c.dedupe_key == event.idempotency_key,
                    )
                ).first()
                if existing is None:
                    self.session.execute(inbox_message.insert().values(**values))
            row = (
                self.session.execute(
                    select(inbox_message)
                    .where(
                        inbox_message.c.tenant_id == scope.tenant_id,
                        inbox_message.c.consumer_name == self.consumer_name,
                        inbox_message.c.dedupe_key == event.idempotency_key,
                    )
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if row["event_id"] != event.event_id or row["payload"] != payload:
                raise IdempotencyConflict("inbox dedupe key was reused by a different message")
            if row["processing_status"] == "PROCESSED":
                result = InboxResult(message_id=row["id"], processed=False, duplicate=True)
            else:
                _handler_call(handler, self.session, event)
                self.session.execute(
                    update(inbox_message)
                    .where(inbox_message.c.id == row["id"], inbox_message.c.tenant_id == scope.tenant_id)
                    .values(processing_status="PROCESSED", processed_at=datetime.now(UTC), last_error=None)
                )
                result = InboxResult(message_id=row["id"], processed=True, duplicate=False)
            if transaction is not None:
                transaction.commit()
            return result
        except BaseException:
            if transaction is not None:
                transaction.rollback()
            raise


__all__ = ["InboxConsumer", "InboxResult"]
