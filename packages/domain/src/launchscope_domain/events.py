"""Versioned domain event envelope and event construction helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID, uuid4

from .enums import EventType
from .errors import ValidationError
from .value_objects import CorrelationContext, TenantScope

_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z0-9_-]+)+$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


def _uuid(value: UUID | str, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must be a UUID", details={"field": field_name}) from exc


def _iso(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _json_value(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _iso(value)
    if hasattr(value, "value") and isinstance(value.value, str):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """The transport-neutral envelope frozen by the contracts package."""

    event_type: str | EventType
    tenant_id: UUID | str
    run_id: UUID | str
    payload: Mapping[str, Any]
    correlation_id: UUID | str
    idempotency_key: str
    task_id: UUID | str | None = None
    causation_id: UUID | str | None = None
    schema_version: str = "1.0"
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: UUID | str = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        event_name = self.event_type.value if isinstance(self.event_type, EventType) else self.event_type
        if not isinstance(event_name, str) or _EVENT_NAME.fullmatch(event_name) is None:
            raise ValidationError("event_type must be a dotted lower-case event name", details={"field": "event_type"})
        object.__setattr__(self, "event_type", event_name)
        for field_name in ("tenant_id", "run_id", "correlation_id", "event_id"):
            object.__setattr__(self, field_name, _uuid(getattr(self, field_name), field_name))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", _uuid(self.task_id, "task_id"))
        if self.causation_id is not None:
            object.__setattr__(self, "causation_id", _uuid(self.causation_id, "causation_id"))
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise ValidationError("idempotency_key must be non-empty", details={"field": "idempotency_key"})
        if _IDEMPOTENCY_KEY.fullmatch(self.idempotency_key) is None:
            raise ValidationError(
                "idempotency_key has invalid characters or length",
                details={"field": "idempotency_key"},
            )
        if not isinstance(self.schema_version, str) or re.fullmatch(r"[0-9]+\.[0-9]+", self.schema_version) is None:
            raise ValidationError("schema_version must use MAJOR.MINOR form", details={"field": "schema_version"})
        if (
            not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise ValidationError("occurred_at must be timezone-aware", details={"field": "occurred_at"})
        if not isinstance(self.payload, Mapping):
            raise ValidationError("payload must be an object", details={"field": "payload"})
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    @classmethod
    def create(
        cls,
        event_type: str | EventType,
        scope: TenantScope,
        context: CorrelationContext,
        payload: Mapping[str, Any],
        *,
        task_id: UUID | str | None = None,
        occurred_at: datetime | None = None,
    ) -> EventEnvelope:
        """Construct an event from the two scope value objects."""

        if scope.run_id is None:
            raise ValidationError("an event requires scope.run_id", details={"field": "run_id"})
        return cls(
            event_type=event_type,
            tenant_id=scope.tenant_id,
            run_id=scope.run_id,
            task_id=task_id,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
            idempotency_key=context.idempotency_key,
            schema_version=context.schema_version,
            occurred_at=occurred_at or datetime.now(UTC),
            payload=payload,
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize to the exact JSON-compatible envelope shape."""

        return {
            "event_type": self.event_type,
            "event_id": str(self.event_id),
            "tenant_id": str(self.tenant_id),
            "run_id": str(self.run_id),
            "task_id": str(self.task_id) if self.task_id is not None else None,
            "correlation_id": str(self.correlation_id),
            "causation_id": str(self.causation_id) if self.causation_id is not None else None,
            "idempotency_key": self.idempotency_key,
            "schema_version": self.schema_version,
            "occurred_at": _iso(self.occurred_at),
            "payload": cast(dict[str, object], _json_value(self.payload)),
        }


DomainEvent = EventEnvelope
Event = EventEnvelope


def build_event(
    event_type: str | EventType,
    scope: TenantScope,
    context: CorrelationContext,
    payload: Mapping[str, Any],
    *,
    task_id: UUID | str | None = None,
    occurred_at: datetime | None = None,
) -> DomainEvent:
    """Small functional factory useful to application adapters."""

    return EventEnvelope.create(
        event_type,
        scope,
        context,
        payload,
        task_id=task_id,
        occurred_at=occurred_at,
    )


class DomainEventFactory:
    """Named constructors for the initial event vocabulary."""

    @staticmethod
    def create(
        event_type: str | EventType,
        scope: TenantScope,
        context: CorrelationContext,
        payload: Mapping[str, Any],
        *,
        task_id: UUID | str | None = None,
        occurred_at: datetime | None = None,
    ) -> DomainEvent:
        return build_event(event_type, scope, context, payload, task_id=task_id, occurred_at=occurred_at)

    @staticmethod
    def project_created(scope: TenantScope, context: CorrelationContext, payload: Mapping[str, Any]) -> DomainEvent:
        return build_event(EventType.PROJECT_CREATED, scope, context, payload)

    @staticmethod
    def product_version_submitted(
        scope: TenantScope, context: CorrelationContext, payload: Mapping[str, Any]
    ) -> DomainEvent:
        return build_event(EventType.PRODUCT_VERSION_SUBMITTED, scope, context, payload)

    @staticmethod
    def evaluation_run_started(
        scope: TenantScope, context: CorrelationContext, payload: Mapping[str, Any]
    ) -> DomainEvent:
        return build_event(EventType.EVALUATION_RUN_STARTED, scope, context, payload)

    @staticmethod
    def run_needs_attention(scope: TenantScope, context: CorrelationContext, payload: Mapping[str, Any]) -> DomainEvent:
        return build_event(EventType.RUN_NEEDS_ATTENTION, scope, context, payload)
