# ruff: noqa: B008
"""Executable FastAPI control plane with PostgreSQL as the runtime truth."""

from __future__ import annotations

import logging
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
from .modules.decision_report.export_application import ReportExportBusyError, ReportExportIntegrityError
from .modules.evaluation.canonical_event_recovery import CanonicalEventRecoveryError
from .modules.evaluation.execution_control import (
    RunControlConflictError,
    RunExecutionPausedError,
    RunNotPausableError,
    RunNotRecoverableError,
    RunNotResumableError,
)
from .modules.evaluation.intake_application import IntakeValidationError
from .modules.evaluation.limit_amendment_application import (
    RunLimitAmendmentConflict,
    RunLimitAmendmentError,
)
from .modules.identity_tenant.application import Actor, AuthorizationError, IdentityTenantApplication, NotFoundError
from .modules.project_dossier.application import ProjectDossierApplication
from .modules.project_dossier.material_ingestion import MaterialValidationError
from .modules.project_dossier.persistent_application import (
    PersistentIdentityTenantApplication,
    PersistentProjectDossierApplication,
)
from .modules.supervisor.audit_application import SupervisorAuditApplication
from .modules.supervisor.completion_application import SupervisorCompletionApplication
from .modules.supervisor.conversation_application import RunConversationApplication
from .modules.supervisor.intake_application import SupervisorChatApplication
from .modules.supervisor.planning_application import ManagerPlanningApplication
from .modules.user_validation.application import (
    ArtifactIntegrityError,
    IdempotencyConflictError,
    ReportTooLargeError,
)
from .modules.user_validation.runner import RunnerUnavailableError

_LOGGER = logging.getLogger(__name__)


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
    supervisor: SupervisorChatApplication | None = None
    run_conversations: RunConversationApplication | None = None
    manager_planning: ManagerPlanningApplication | None = None
    supervisor_audit: SupervisorAuditApplication | None = None
    supervisor_completion: SupervisorCompletionApplication | None = None

    @classmethod
    def from_env(cls) -> PersistentControlPlane:
        settings = DatabaseSettings.from_env()
        sessions = session_factory(create_database_engine(settings.url, application_role="launchscope_runtime"))
        identity = PersistentIdentityTenantApplication(sessions)
        objects = S3QuarantineObjectStore.from_env()
        supervisor = SupervisorChatApplication(sessions, objects)
        return cls(
            identity=identity,
            dossier=PersistentProjectDossierApplication(sessions, identity, objects),
            supervisor=supervisor,
            run_conversations=RunConversationApplication(sessions, objects, supervisor),
            manager_planning=ManagerPlanningApplication(sessions),
            supervisor_audit=SupervisorAuditApplication(sessions, objects),
            supervisor_completion=SupervisorCompletionApplication(sessions, objects),
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
            allow_methods=["GET", "POST", "PUT", "OPTIONS"],
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
    from .modules.supervisor.api import router as supervisor_router
    from .modules.user_validation.api import router as user_validation_router

    app.include_router(identity_router, prefix="/api/v1")
    app.include_router(dossier_router, prefix="/api/v1")
    app.include_router(experience_router, prefix="/api/v1")
    app.include_router(user_validation_router, prefix="/api/v1")
    app.include_router(supervisor_router, prefix="/api/v1")
    if demo_mode:
        from .modules.identity_tenant.demo_api import build_demo_router

        app.include_router(build_demo_router(), prefix="/api/v1")
    if os.getenv("LAUNCHSCOPE_AGENTTEAMS_BRIDGE_ENABLED", "").lower() == "true":
        from .modules.evaluation.agentteams_api import router as agentteams_router

        app.include_router(agentteams_router, prefix="/api/v1")

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        """Keep database/schema failures visible to browsers without leaking SQL details."""

        _LOGGER.exception("database request failed for %s", request.url.path, exc_info=exc)

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
            if str(exc).startswith("SUPERVISOR_1P4_DISABLED:"):
                status, code = 503, "SUPERVISOR_1P4_DISABLED"
            elif str(exc).startswith("EXECUTION_RUNTIME_UNAVAILABLE:"):
                status, code = 503, "EXECUTION_RUNTIME_UNAVAILABLE"
            else:
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

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict(request: Request, exc: IdempotencyConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error_code": "IDEMPOTENCY_CONFLICT",
                "message": str(exc),
                "correlation_id": request.headers.get("X-Correlation-Id", ""),
                "retryable": False,
                "details": {},
            },
        )

    @app.exception_handler(ReportExportBusyError)
    @app.exception_handler(ReportExportIntegrityError)
    async def report_export_conflict(request: Request, exc: Exception) -> JSONResponse:
        code = "REPORT_EXPORT_INTEGRITY" if isinstance(exc, ReportExportIntegrityError) else "REPORT_EXPORT_NOT_READY"
        return JSONResponse(
            status_code=409,
            content={
                "error_code": code,
                "message": str(exc),
                "correlation_id": request.headers.get("X-Correlation-Id", ""),
                "retryable": False,
                "details": {},
            },
        )

    @app.exception_handler(RunControlConflictError)
    @app.exception_handler(CanonicalEventRecoveryError)
    @app.exception_handler(RunLimitAmendmentConflict)
    @app.exception_handler(RunLimitAmendmentError)
    @app.exception_handler(RunNotPausableError)
    @app.exception_handler(RunNotRecoverableError)
    @app.exception_handler(RunNotResumableError)
    async def run_control_conflict(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, CanonicalEventRecoveryError):
            code = "CANONICAL_EVENT_RECOVERY_NOT_ALLOWED"
        elif isinstance(exc, RunLimitAmendmentConflict):
            code = "RUN_LIMIT_AMENDMENT_CONFLICT"
        elif isinstance(exc, RunLimitAmendmentError):
            code = "RUN_LIMIT_AMENDMENT_NOT_ALLOWED"
        elif isinstance(exc, RunNotPausableError):
            code = "RUN_NOT_PAUSABLE"
        elif isinstance(exc, RunNotRecoverableError):
            code = "RUN_NOT_RECOVERABLE"
        elif isinstance(exc, RunNotResumableError):
            code = "RUN_NOT_RESUMABLE"
        else:
            code = "RUN_CONTROL_CONFLICT"
        return JSONResponse(
            status_code=409,
            content={
                "error_code": code,
                "message": str(exc),
                "correlation_id": request.headers.get("X-Correlation-Id", ""),
                "retryable": False,
                "details": {},
            },
        )

    @app.exception_handler(RunExecutionPausedError)
    async def run_execution_paused(request: Request, exc: RunExecutionPausedError) -> JSONResponse:
        return JSONResponse(
            status_code=423,
            content={
                "error_code": "RUN_PAUSED",
                "message": str(exc),
                "correlation_id": request.headers.get("X-Correlation-Id", ""),
                "retryable": False,
                "details": {},
            },
        )

    @app.exception_handler(ArtifactIntegrityError)
    async def artifact_integrity_error(request: Request, exc: ArtifactIntegrityError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error_code": "ARTIFACT_INTEGRITY_MISMATCH",
                "message": str(exc),
                "correlation_id": request.headers.get("X-Correlation-Id", ""),
                "retryable": False,
                "details": {},
            },
        )

    @app.exception_handler(ReportTooLargeError)
    async def report_too_large(request: Request, exc: ReportTooLargeError) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={
                "error_code": "REPORT_TOO_LARGE",
                "message": str(exc),
                "correlation_id": request.headers.get("X-Correlation-Id", ""),
                "retryable": False,
                "details": {},
            },
        )

    @app.exception_handler(RunnerUnavailableError)
    async def runner_unavailable(request: Request, exc: RunnerUnavailableError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error_code": "DEPENDENCY_UNAVAILABLE",
                "message": str(exc),
                "correlation_id": request.headers.get("X-Correlation-Id", ""),
                "retryable": True,
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
