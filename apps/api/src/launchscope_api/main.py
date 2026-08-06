# ruff: noqa: B008
"""Executable FastAPI control plane with PostgreSQL as the runtime truth."""

from __future__ import annotations

import os
from dataclasses import dataclass
from uuid import UUID

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from launchscope_domain.errors import DomainError

from .infrastructure.db.session import DatabaseSettings, create_database_engine, session_factory
from .infrastructure.object_store import S3QuarantineObjectStore
from .modules.evaluation.intake_application import IntakeValidationError
from .modules.identity_tenant.application import Actor, AuthorizationError, IdentityTenantApplication, NotFoundError
from .modules.project_dossier.application import ProjectDossierApplication
from .modules.project_dossier.material_ingestion import MaterialValidationError
from .modules.project_dossier.persistent_application import (
    PersistentIdentityTenantApplication,
    PersistentProjectDossierApplication,
)


@dataclass
class ControlPlane:
    """Explicit in-memory test double; never selected by the default runtime."""

    identity: IdentityTenantApplication
    dossier: ProjectDossierApplication

    @classmethod
    def create(cls) -> ControlPlane:
        identity = IdentityTenantApplication()
        return cls(identity=identity, dossier=ProjectDossierApplication(identity))


@dataclass
class PersistentControlPlane:
    """Default runtime control plane backed by PostgreSQL and private S3 storage."""

    identity: PersistentIdentityTenantApplication
    dossier: PersistentProjectDossierApplication

    @classmethod
    def from_env(cls) -> PersistentControlPlane:
        settings = DatabaseSettings.from_env()
        sessions = session_factory(create_database_engine(settings.url, application_role="launchscope_runtime"))
        identity = PersistentIdentityTenantApplication(sessions)
        return cls(
            identity=identity,
            dossier=PersistentProjectDossierApplication(sessions, identity, S3QuarantineObjectStore.from_env()),
        )


_persistent_control_plane: PersistentControlPlane | None = None


def get_control_plane() -> PersistentControlPlane:
    """Lazily construct the durable runtime adapter after configuration exists."""

    global _persistent_control_plane
    if _persistent_control_plane is None:
        _persistent_control_plane = PersistentControlPlane.from_env()
    return _persistent_control_plane


def request_actor(
    x_tenant_id: UUID = Header(alias="X-Tenant-Id"),
    x_actor_id: str = Header(alias="X-Actor-Id", min_length=1, max_length=255),
) -> Actor:
    return Actor(tenant_id=x_tenant_id, actor_id=x_actor_id)


def create_app(control_plane: ControlPlane | PersistentControlPlane | None = None) -> FastAPI:
    """Build an app; only an explicit argument may select the in-memory double."""

    app = FastAPI(title="LaunchScope Control Plane", version="1.0.0")
    cors_origins = tuple(
        value.strip() for value in os.getenv("LAUNCHSCOPE_CORS_ORIGINS", "").split(",") if value.strip()
    )
    demo_mode = os.getenv("LAUNCHSCOPE_DEMO_MODE", "").lower() == "true"
    if demo_mode:
        from .modules.identity_tenant.demo_api import configured_demo_origins

        cors_origins = tuple(dict.fromkeys((*cors_origins, *configured_demo_origins())))
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=[
                "Content-Type",
                "Idempotency-Key",
                "Last-Event-ID",
                "X-Actor-Id",
                "X-Correlation-Id",
                "X-Ops-Actor-Id",
                "X-Tenant-Id",
                "X-Workspace-Id",
            ],
        )
    if control_plane is not None:
        app.state.control_plane = control_plane

    from .modules.experience.api import router as experience_router
    from .modules.identity_tenant.api import router as identity_router
    from .modules.project_dossier.api import router as dossier_router

    app.include_router(identity_router, prefix="/api/v1")
    app.include_router(dossier_router, prefix="/api/v1")
    app.include_router(experience_router, prefix="/api/v1")
    if demo_mode:
        from .modules.identity_tenant.demo_api import build_demo_router

        app.include_router(build_demo_router(), prefix="/api/v1")
    if os.getenv("LAUNCHSCOPE_AGENTTEAMS_BRIDGE_ENABLED", "").lower() == "true":
        from .modules.evaluation.agentteams_api import router as agentteams_router

        app.include_router(agentteams_router, prefix="/api/v1")

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, _exc: SQLAlchemyError) -> JSONResponse:
        """Keep database/schema failures visible to browsers without leaking SQL details."""

        return JSONResponse(
            status_code=503,
            content={
                "error_code": "DATABASE_UNAVAILABLE",
                "message": "Database unavailable or schema is outdated. Run the Demo database migration, then retry.",
                "correlation_id": request.headers.get("X-Correlation-Id", ""),
                "retryable": False,
                "details": {},
            },
        )

    @app.exception_handler(AuthorizationError)
    @app.exception_handler(NotFoundError)
    @app.exception_handler(ValueError)
    @app.exception_handler(DomainError)
    async def control_plane_error(request: Request, exc: Exception) -> JSONResponse:
        correlation_id = request.headers.get("X-Correlation-Id", "")
        if isinstance(exc, AuthorizationError):
            status, code = 403, "FORBIDDEN"
        elif isinstance(exc, NotFoundError):
            status, code = 404, "NOT_FOUND"
        elif isinstance(exc, (MaterialValidationError, IntakeValidationError, DomainError)):
            status, code = 422, "PRECONDITION_FAILED"
        else:
            status, code = 400, "VALIDATION_ERROR"
        return JSONResponse(
            status_code=status,
            content={
                "error_code": code,
                "message": str(exc),
                "correlation_id": correlation_id,
                "retryable": False,
                "details": {},
            },
        )

    return app


app = create_app()

__all__ = [
    "ControlPlane",
    "PersistentControlPlane",
    "app",
    "create_app",
    "get_control_plane",
    "request_actor",
]
