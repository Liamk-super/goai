"""Scope-first policy for project Memory and Evidence RAG."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, or_

from launchscope_api.infrastructure.db.schema import memory_item
from launchscope_domain.value_objects import TenantScope


class RetrievalPolicyError(ValueError):
    """The caller did not provide enough scope to retrieve safely."""


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    scope: TenantScope
    region: str
    permissions: frozenset[str]
    at: datetime

    @classmethod
    def create(
        cls,
        scope: TenantScope,
        *,
        region: str,
        permissions: set[str] | frozenset[str],
        at: datetime | None = None,
    ) -> RetrievalScope:
        if scope.project_id is None or scope.product_version_id is None:
            raise RetrievalPolicyError("RAG requires tenant, project and product version scope")
        if not region.strip():
            raise RetrievalPolicyError("RAG requires a region")
        if not permissions:
            raise RetrievalPolicyError("RAG requires explicit caller permissions")
        moment = at.astimezone(UTC) if at is not None else datetime.now(UTC)
        return cls(scope=scope, region=region.strip(), permissions=frozenset(permissions), at=moment)


class RetrievalPolicy:
    """Build fail-closed SQL predicates before any lexical/vector ranking."""

    def predicates(self, request: RetrievalScope) -> tuple[ColumnElement[bool], ...]:
        return (
            memory_item.c.tenant_id == request.scope.tenant_id,
            memory_item.c.project_id == request.scope.project_id,
            memory_item.c.product_version_id == request.scope.product_version_id,
            memory_item.c.region == request.region,
            memory_item.c.permission_scope.in_(request.permissions),
            memory_item.c.validity_status == "ACTIVE",
            or_(memory_item.c.valid_until.is_(None), memory_item.c.valid_until >= request.at),
        )

    def permits_row(self, request: RetrievalScope, row: dict[str, object]) -> bool:
        """A defensive second check for adapters that do not use SQLAlchemy."""

        valid_until = row.get("valid_until")
        is_current = valid_until is None or (isinstance(valid_until, datetime) and valid_until >= request.at)
        return bool(
            row.get("tenant_id") == request.scope.tenant_id
            and row.get("project_id") == request.scope.project_id
            and row.get("product_version_id") == request.scope.product_version_id
            and row.get("region") == request.region
            and row.get("permission_scope") in request.permissions
            and row.get("validity_status") == "ACTIVE"
            and is_current
        )


__all__ = ["RetrievalPolicy", "RetrievalPolicyError", "RetrievalScope"]
