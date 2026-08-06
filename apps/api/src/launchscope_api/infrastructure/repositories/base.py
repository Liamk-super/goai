"""Shared helpers for SQLAlchemy repository adapters."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import Select, Table, select
from sqlalchemy.orm import Session

from launchscope_domain.errors import TenantScopeViolation
from launchscope_domain.value_objects import TenantScope


def json_value(value: object) -> object:
    """Convert domain values to JSON-compatible values without storing bodies."""

    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_value(item) for item in value]
    return value


def utc_datetime(value: datetime | None) -> datetime | None:
    """Normalize driver timestamps for domain value objects.

    PostgreSQL returns aware timestamptz values.  SQLite test doubles do not
    preserve timezone metadata, so treating a naive value as UTC keeps the
    adapter contract deterministic without changing the domain package.
    """

    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def require_utc_datetime(value: datetime | None) -> datetime:
    normalized = utc_datetime(value)
    if normalized is None:
        raise ValueError("required database timestamp is null")
    return normalized


def assert_aggregate_scope(aggregate: object, scope: TenantScope) -> None:
    aggregate_scope = getattr(aggregate, "scope", None)
    if aggregate_scope is None or not isinstance(aggregate_scope, TenantScope):
        raise TypeError("aggregate must expose a TenantScope")
    if aggregate_scope.tenant_id != scope.tenant_id or not scope.contains(aggregate_scope):
        raise TenantScopeViolation(
            "repository aggregate is outside the transaction tenant scope",
            details={
                "expected_tenant_id": str(scope.tenant_id),
                "actual_tenant_id": str(aggregate_scope.tenant_id),
            },
        )


def scoped_select(table: Table, scope: TenantScope) -> Select[tuple[Any]]:
    """Build an explicit tenant predicate in addition to database RLS."""

    return select(table).where(table.c.tenant_id == scope.tenant_id)


def existing_row(session: Session, table: Table, resource_id: UUID, scope: TenantScope) -> Any | None:
    return (
        session.execute(select(table).where(table.c.id == resource_id, table.c.tenant_id == scope.tenant_id))
        .mappings()
        .first()
    )


def insert_if_absent(session: Session, table: Table, values: Mapping[str, object], *, resource_id: UUID) -> bool:
    """Insert one fact and report whether this call created it.

    Append-only adapters never update an existing historical row.  Mutable
    aggregate roots use a separate explicit UPDATE after this helper.
    """

    if session.execute(select(table.c.id).where(table.c.id == resource_id)).first() is not None:
        return False
    session.execute(table.insert().values(**values))
    return True


def require_scope_id(scope: TenantScope, field_name: str) -> UUID:
    value = getattr(scope, field_name)
    if value is None:
        raise ValueError(f"scope.{field_name} is required")
    return value


__all__ = [
    "assert_aggregate_scope",
    "existing_row",
    "insert_if_absent",
    "json_value",
    "require_scope_id",
    "require_utc_datetime",
    "scoped_select",
    "utc_datetime",
]
