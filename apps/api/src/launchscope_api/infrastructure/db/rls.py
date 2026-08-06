"""Tenant context and RLS helpers.

RLS is a database boundary, not an authorization substitute in the domain.
Every request transaction must set the tenant context with ``SET LOCAL``.  A
missing context therefore matches no tenant policy and is fail-closed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Connection, text

from launchscope_domain.value_objects import TenantScope

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


@dataclass(frozen=True, slots=True)
class TenantContext:
    """The request-scoped values visible to PostgreSQL RLS policies."""

    scope: TenantScope
    actor_id: str | None = None


def _setting(value: UUID | str | None) -> str:
    return str(value) if value is not None else ""


def set_local_tenant_context(
    connection: Connection,
    scope: TenantScope,
    *,
    actor_id: str | None = None,
) -> TenantContext:
    """Set all scope values for the current transaction.

    ``set_config(..., true)`` is the parameterized form of ``SET LOCAL`` and
    cannot leak a tenant into the next transaction when a pooled connection is
    reused.  The helper intentionally rejects a bare UUID so callers cannot
    accidentally omit the required domain scope object.
    """

    if not isinstance(scope, TenantScope):
        raise TypeError("scope must be a TenantScope")
    if connection.dialect.name != "postgresql":
        # SQLite is useful for adapter unit tests, but it cannot emulate
        # PostgreSQL RLS.  Repositories still add explicit tenant predicates;
        # integration/security tests must run against PostgreSQL.
        return TenantContext(scope=scope, actor_id=actor_id)
    values: Mapping[str, str] = {
        "app.tenant_id": _setting(scope.tenant_id),
        "app.workspace_id": _setting(scope.workspace_id),
        "app.project_id": _setting(scope.project_id),
        "app.product_version_id": _setting(scope.product_version_id),
        "app.run_id": _setting(scope.run_id),
        "app.actor_id": actor_id or "",
    }
    for key, value in values.items():
        connection.execute(text("SELECT set_config(:key, :value, true)"), {"key": key, "value": value})
    return TenantContext(scope=scope, actor_id=actor_id)


def clear_local_tenant_context(connection: Connection) -> None:
    """Clear context values inside the current transaction when needed."""

    if connection.dialect.name != "postgresql":
        return
    for key in (
        "app.tenant_id",
        "app.workspace_id",
        "app.project_id",
        "app.product_version_id",
        "app.run_id",
        "app.actor_id",
    ):
        connection.execute(text("SELECT set_config(:key, '', true)"), {"key": key})


def validate_runtime_role(role: str | None) -> str | None:
    """Validate an optional role before it is interpolated into ``SET ROLE``."""

    if role is None:
        return None
    if _ROLE_NAME.fullmatch(role) is None:
        raise ValueError("application database role contains invalid characters")
    return role


__all__ = [
    "TenantContext",
    "clear_local_tenant_context",
    "set_local_tenant_context",
    "validate_runtime_role",
]
