# ruff: noqa: B008
"""FastAPI router for tenant and workspace creation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from launchscope_api.modules.project_dossier.persistent_application import PersistentIdentityTenantApplication

from .application import Actor, IdentityTenantApplication

router = APIRouter(tags=["Identity"])


class CreateTenantRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=120)
    workspace_name: str = Field(min_length=1, max_length=200)


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def get_identity(request: Request) -> IdentityTenantApplication | PersistentIdentityTenantApplication:
    configured = getattr(request.app.state, "control_plane", None)
    if configured is not None:
        return configured.identity
    from launchscope_api.main import get_control_plane

    return get_control_plane().identity


def get_actor() -> Actor:
    from launchscope_api.main import request_actor

    return request_actor()


@router.post("/tenants", status_code=201)
def create_tenant(
    request: CreateTenantRequest,
    http_request: Request,
    x_actor_id: str = Header(alias="X-Actor-Id", min_length=1, max_length=255),
) -> dict[str, str]:
    tenant, workspace = get_identity(http_request).create_tenant(request.slug, x_actor_id, request.workspace_name)
    return {"tenant_id": str(tenant.tenant_id), "workspace_id": str(workspace.workspace_id), "slug": tenant.slug}


@router.post("/workspaces", status_code=201)
def create_workspace(
    request: CreateWorkspaceRequest,
    actor: Actor = Depends(get_actor),
    identity: IdentityTenantApplication | PersistentIdentityTenantApplication = Depends(get_identity),
) -> dict[str, str]:
    workspace = identity.create_workspace(actor, request.name)
    return {"workspace_id": str(workspace.workspace_id), "tenant_id": str(workspace.tenant_id), "name": workspace.name}


__all__ = ["router"]
