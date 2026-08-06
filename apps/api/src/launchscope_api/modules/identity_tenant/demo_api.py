# ruff: noqa: B008
"""Local-only Demo identity routes; this module is never production auth."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field, field_validator

from launchscope_api.modules.project_dossier.persistent_application import PersistentIdentityTenantApplication

from .api import get_identity
from .application import Actor, IdentityTenantApplication, WorkspaceRole

DEMO_SESSION_SCHEMA = "launchscope.demo.session.v1"


class CreateDemoSessionRequest(BaseModel):
    display_name: str = Field(min_length=2, max_length=40)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not 2 <= len(normalized) <= 40:
            raise ValueError("display_name must contain 2 to 40 non-whitespace characters")
        return normalized


def configured_demo_origins() -> frozenset[str]:
    configured = os.getenv(
        "LAUNCHSCOPE_DEMO_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000",
    )
    origins = frozenset(value.strip().rstrip("/") for value in configured.split(",") if value.strip())
    if not origins:
        raise RuntimeError("LAUNCHSCOPE_DEMO_ORIGINS must contain at least one loopback Origin")
    for origin in origins:
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("Demo session Origins must be explicit loopback HTTP(S) origins")
    return origins


def _require_demo_origin(request: Request) -> None:
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin not in configured_demo_origins():
        from .application import AuthorizationError

        raise AuthorizationError("Demo sessions require an allowed local Origin")


def build_demo_router() -> APIRouter:
    router = APIRouter(tags=["Local Demo Identity"])

    @router.post("/demo/sessions", status_code=201)
    def create_demo_session(payload: CreateDemoSessionRequest, request: Request) -> dict[str, str]:
        _require_demo_origin(request)
        identity: IdentityTenantApplication | PersistentIdentityTenantApplication = get_identity(request)
        nonce = uuid4()
        actor_id = f"local-demo:{nonce}"
        tenant, workspace = identity.create_tenant(
            f"demo-{nonce.hex}",
            actor_id,
            f"{payload.display_name} Demo Workspace",
        )
        return {
            "schemaVersion": DEMO_SESSION_SCHEMA,
            "tenantId": str(tenant.tenant_id),
            "workspaceId": str(workspace.workspace_id),
            "actorId": actor_id,
            "displayName": payload.display_name,
            "createdAt": datetime.now(UTC).isoformat(),
        }

    @router.get("/demo/session")
    def validate_demo_session(
        request: Request,
        x_tenant_id: UUID = Header(alias="X-Tenant-Id"),
        x_actor_id: str = Header(alias="X-Actor-Id", min_length=1, max_length=255),
        x_workspace_id: UUID = Header(alias="X-Workspace-Id"),
    ) -> dict[str, str | bool]:
        _require_demo_origin(request)
        if not x_actor_id.startswith("local-demo:"):
            from .application import AuthorizationError

            raise AuthorizationError("The actor is not a local Demo identity")
        identity: IdentityTenantApplication | PersistentIdentityTenantApplication = get_identity(request)
        identity.require_workspace_role(Actor(x_tenant_id, x_actor_id), x_workspace_id, WorkspaceRole.VIEWER)
        return {
            "valid": True,
            "schemaVersion": DEMO_SESSION_SCHEMA,
            "tenantId": str(x_tenant_id),
            "workspaceId": str(x_workspace_id),
            "actorId": x_actor_id,
        }

    return router


__all__ = ["DEMO_SESSION_SCHEMA", "build_demo_router", "configured_demo_origins"]
