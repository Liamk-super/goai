"""Transactional Outbox adapter and committed-message publisher boundary."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.orm import Session

from launchscope_domain.errors import ValidationError
from launchscope_domain.events import EventEnvelope
from launchscope_domain.value_objects import TenantScope

from ..db.schema import outbox_message

_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "messages",
        "chat_history",
        "conversation",
        "prompt",
        "system_prompt",
        "private_reasoning",
        "chain_of_thought",
        "thoughts",
        "access_token",
        "refresh_token",
    }
)


class IdempotencyConflict(ValidationError):
    """A key was reused with a different event fact."""

    code = "IDEMPOTENCY_CONFLICT"


class MessagePolicyViolation(ValidationError):
    """A message contains body/reasoning material outside the envelope policy."""

    code = "MESSAGE_POLICY_VIOLATION"


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    id: UUID
    tenant_id: UUID
    aggregate_id: UUID
    aggregate_type: str
    event_type: str
    event_id: UUID
    schema_version: str
    idempotency_key: str
    payload: Mapping[str, Any]
    publish_status: str
    available_at: datetime
    published_at: datetime | None
    attempts: int
    claimed_by: str | None
    claimed_at: datetime | None


def _assert_safe_payload(value: object, *, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in _FORBIDDEN_PAYLOAD_KEYS:
                raise MessagePolicyViolation(
                    f"message payload field is not allowed: {path}.{key}",
                    details={"field": f"{path}.{key}"},
                )
            _assert_safe_payload(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_safe_payload(child, path=f"{path}[{index}]")


def _record(row: Mapping[str, Any]) -> OutboxRecord:
    return OutboxRecord(
        id=row["id"],
        tenant_id=row["tenant_id"],
        aggregate_id=row["aggregate_id"],
        aggregate_type=row["aggregate_type"],
        event_type=row["event_type"],
        event_id=row["event_id"],
        schema_version=row["schema_version"],
        idempotency_key=row["idempotency_key"],
        payload=row["payload"],
        publish_status=row["publish_status"],
        available_at=row["available_at"],
        published_at=row["published_at"],
        attempts=row["attempts"],
        claimed_by=row.get("claimed_by"),
        claimed_at=row.get("claimed_at"),
    )


class OutboxRepository:
    """Write and transition messages in the caller's current transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(
        self,
        event: EventEnvelope,
        *,
        aggregate_id: UUID | str | None = None,
        aggregate_type: str = "run",
        scope: TenantScope | None = None,
        available_at: datetime | None = None,
    ) -> OutboxRecord:
        if scope is not None and UUID(str(event.tenant_id)) != scope.tenant_id:
            raise IdempotencyConflict("event tenant does not match transaction tenant")
        if _NAME.fullmatch(aggregate_type) is None:
            raise ValidationError("aggregate_type contains invalid characters")
        payload = event.to_dict()
        _assert_safe_payload(payload)
        if len(json.dumps(payload, sort_keys=True, separators=(",", ":"))) > 65536:
            raise MessagePolicyViolation("outbox payload exceeds the structured envelope limit")
        tenant_id = UUID(str(event.tenant_id))
        resolved_aggregate_id = UUID(str(aggregate_id or event.run_id))
        now = datetime.now(UTC)
        values = {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "aggregate_id": resolved_aggregate_id,
            "aggregate_type": aggregate_type,
            "event_type": event.event_type,
            "event_id": event.event_id,
            "schema_version": event.schema_version,
            "idempotency_key": event.idempotency_key,
            "payload": payload,
            "publish_status": "PENDING",
            "available_at": available_at or now,
            "attempts": 0,
            "occurred_at": event.occurred_at,
            "created_at": now,
        }
        dialect = self.session.bind.dialect.name if self.session.bind is not None else ""
        if dialect == "postgresql":
            statement = (
                postgres_insert(outbox_message)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
            )
            self.session.execute(statement)
        else:
            existing = (
                self.session.execute(
                    select(outbox_message).where(
                        outbox_message.c.tenant_id == tenant_id,
                        outbox_message.c.idempotency_key == event.idempotency_key,
                    )
                )
                .mappings()
                .first()
            )
            if existing is None:
                self.session.execute(outbox_message.insert().values(**values))
        row = (
            self.session.execute(
                select(outbox_message).where(
                    outbox_message.c.tenant_id == tenant_id,
                    outbox_message.c.idempotency_key == event.idempotency_key,
                )
            )
            .mappings()
            .one()
        )
        if row["event_id"] != event.event_id or row["payload"] != payload:
            raise IdempotencyConflict(
                "idempotency_key was already used by a different event",
                details={"idempotency_key": event.idempotency_key},
            )
        return _record(dict(row))

    def get(self, message_id: UUID | str, scope: TenantScope) -> OutboxRecord | None:
        row = (
            self.session.execute(
                select(outbox_message).where(
                    outbox_message.c.id == UUID(str(message_id)),
                    outbox_message.c.tenant_id == scope.tenant_id,
                )
            )
            .mappings()
            .first()
        )
        return _record(dict(row)) if row else None

    def claim_committed(
        self,
        scope: TenantScope,
        *,
        publisher_id: str = "launchscope-publisher",
        limit: int = 50,
        claim_timeout_seconds: int = 120,
    ) -> tuple[OutboxRecord, ...]:
        """Claim rows visible to this transaction.

        A publisher must call this from a fresh transaction/session after the
        state transaction commits.  PostgreSQL row locks make multiple
        publishers safe without publishing an uncommitted row.
        """

        if limit <= 0:
            return ()
        if _NAME.fullmatch(publisher_id) is None:
            raise ValueError("publisher_id contains invalid characters")
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=max(1, claim_timeout_seconds))
        rows = (
            self.session.execute(
                select(outbox_message)
                .where(
                    outbox_message.c.tenant_id == scope.tenant_id,
                    (
                        outbox_message.c.publish_status.in_(("PENDING", "FAILED"))
                        | (
                            (outbox_message.c.publish_status == "CLAIMED")
                            & (outbox_message.c.claimed_at < stale_before)
                        )
                    ),
                    outbox_message.c.available_at <= now,
                )
                .order_by(outbox_message.c.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            .mappings()
            .all()
        )
        records: list[OutboxRecord] = []
        for row in rows:
            self.session.execute(
                update(outbox_message)
                .where(outbox_message.c.id == row["id"], outbox_message.c.tenant_id == scope.tenant_id)
                .values(
                    publish_status="CLAIMED",
                    attempts=row["attempts"] + 1,
                    claimed_by=publisher_id,
                    claimed_at=now,
                )
            )
            records.append(
                _record(
                    {
                        **row,
                        "publish_status": "CLAIMED",
                        "attempts": row["attempts"] + 1,
                        "claimed_by": publisher_id,
                        "claimed_at": now,
                    }
                )
            )
        return tuple(records)

    def mark_published(
        self, message_id: UUID | str, scope: TenantScope, *, publisher_id: str | None = None
    ) -> None:
        ownership = (
            outbox_message.c.claimed_by == publisher_id
            if publisher_id is not None
            else outbox_message.c.claimed_by.is_not(None)
        )
        result: Any = self.session.execute(
            update(outbox_message)
            .where(
                outbox_message.c.id == UUID(str(message_id)),
                outbox_message.c.tenant_id == scope.tenant_id,
                outbox_message.c.publish_status == "CLAIMED",
                ownership,
            )
            .values(
                publish_status="PUBLISHED",
                published_at=datetime.now(UTC),
                last_error=None,
                claimed_by=None,
                claimed_at=None,
            )
        )
        if result.rowcount != 1:
            raise LookupError("outbox message is not claimed by this tenant")

    def mark_failed(
        self,
        message_id: UUID | str,
        scope: TenantScope,
        error: str,
        *,
        retry_after_seconds: int = 30,
    ) -> None:
        if not error.strip():
            raise ValueError("error is required")
        self.session.execute(
            update(outbox_message)
            .where(
                outbox_message.c.id == UUID(str(message_id)),
                outbox_message.c.tenant_id == scope.tenant_id,
                outbox_message.c.publish_status == "CLAIMED",
            )
            .values(
                publish_status="FAILED",
                last_error=error[:2000],
                available_at=datetime.now(UTC) + timedelta(seconds=max(0, retry_after_seconds)),
                claimed_by=None,
                claimed_at=None,
            )
        )

    def mark_submission_unknown(self, message_id: UUID | str, scope: TenantScope, error: str) -> None:
        """Freeze a possibly submitted message; this state is never eligible for claim/retry."""

        result: Any = self.session.execute(
            update(outbox_message)
            .where(
                outbox_message.c.id == UUID(str(message_id)),
                outbox_message.c.tenant_id == scope.tenant_id,
                outbox_message.c.publish_status == "CLAIMED",
            )
            .values(
                publish_status="SUBMISSION_UNKNOWN", last_error=error[:2000],
                claimed_by=None, claimed_at=None,
            )
        )
        if result.rowcount != 1:
            raise LookupError("outbox message is not in the claimed state")


OutboxMessageRepository = OutboxRepository

__all__ = [
    "IdempotencyConflict",
    "MessagePolicyViolation",
    "OutboxMessageRepository",
    "OutboxRecord",
    "OutboxRepository",
]
