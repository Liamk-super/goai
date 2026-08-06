"""Tenant and workspace authorization used by the T5 control-plane APIs.

The request adapter supplies an authenticated principal.  The small in-memory
store is deliberately injectable: it is the executable local API boundary and
can be replaced by the PostgreSQL adapter without changing the policy checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID, uuid4


class AuthorizationError(PermissionError):
    """The caller is not allowed to access the tenant resource."""


class NotFoundError(LookupError):
    """A resource is absent from the caller's tenant boundary."""


class WorkspaceRole(StrEnum):
    OWNER = "OWNER"
    EDITOR = "EDITOR"
    VIEWER = "VIEWER"


@dataclass(frozen=True, slots=True)
class Actor:
    tenant_id: UUID
    actor_id: str


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: UUID
    slug: str


@dataclass(frozen=True, slots=True)
class Workspace:
    workspace_id: UUID
    tenant_id: UUID
    name: str


@dataclass
class IdentityStore:
    tenants: dict[UUID, Tenant] = field(default_factory=dict)
    workspaces: dict[UUID, Workspace] = field(default_factory=dict)
    memberships: dict[tuple[UUID, UUID, str], WorkspaceRole] = field(default_factory=dict)


class IdentityTenantApplication:
    """Create identity roots and enforce tenant-local workspace membership."""

    def __init__(self, store: IdentityStore | None = None) -> None:
        self.store = store or IdentityStore()

    def create_tenant(self, slug: str, owner_id: str, workspace_name: str) -> tuple[Tenant, Workspace]:
        normalized_slug = _required(slug, "slug", 120)
        normalized_owner = _required(owner_id, "owner_id", 255)
        tenant = Tenant(tenant_id=uuid4(), slug=normalized_slug)
        workspace = Workspace(
            workspace_id=uuid4(),
            tenant_id=tenant.tenant_id,
            name=_required(workspace_name, "name", 200),
        )
        self.store.tenants[tenant.tenant_id] = tenant
        self.store.workspaces[workspace.workspace_id] = workspace
        self.store.memberships[(tenant.tenant_id, workspace.workspace_id, normalized_owner)] = WorkspaceRole.OWNER
        return tenant, workspace

    def create_workspace(self, actor: Actor, name: str) -> Workspace:
        self.require_tenant_member(actor)
        workspace = Workspace(workspace_id=uuid4(), tenant_id=actor.tenant_id, name=_required(name, "name", 200))
        self.store.workspaces[workspace.workspace_id] = workspace
        self.store.memberships[(actor.tenant_id, workspace.workspace_id, actor.actor_id)] = WorkspaceRole.OWNER
        return workspace

    def add_member(self, actor: Actor, workspace_id: UUID, member_id: str, role: WorkspaceRole) -> None:
        self.require_workspace_role(actor, workspace_id, WorkspaceRole.OWNER)
        membership_key = (actor.tenant_id, workspace_id, _required(member_id, "member_id", 255))
        self.store.memberships[membership_key] = WorkspaceRole(role)

    def require_tenant_member(self, actor: Actor) -> None:
        if actor.tenant_id not in self.store.tenants:
            raise NotFoundError("tenant was not found")
        is_member = any(
            tenant_id == actor.tenant_id and member == actor.actor_id for tenant_id, _, member in self.store.memberships
        )
        if not is_member:
            raise AuthorizationError("actor is not a member of this tenant")

    def require_workspace_role(
        self,
        actor: Actor,
        workspace_id: UUID,
        minimum: WorkspaceRole = WorkspaceRole.EDITOR,
    ) -> Workspace:
        workspace = self.store.workspaces.get(workspace_id)
        if workspace is None or workspace.tenant_id != actor.tenant_id:
            raise NotFoundError("workspace was not found")
        role = self.store.memberships.get((actor.tenant_id, workspace_id, actor.actor_id))
        allowed = {
            WorkspaceRole.VIEWER: {WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.OWNER},
            WorkspaceRole.EDITOR: {WorkspaceRole.EDITOR, WorkspaceRole.OWNER},
            WorkspaceRole.OWNER: {WorkspaceRole.OWNER},
        }
        if role not in allowed[minimum]:
            raise AuthorizationError("actor lacks the required workspace role")
        return workspace


def _required(value: str, name: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise ValueError(f"{name} must be a non-empty string up to {max_length} characters")
    return value.strip()
