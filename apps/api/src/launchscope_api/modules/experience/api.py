# ruff: noqa: B008
"""T10 REST/SSE projection endpoints.

The frozen ``control-plane.v1`` transport fields are preserved.  Report,
comparison and Ops resources are documented separately as additive read-only
experience resources instead of changing the original contract in place.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from launchscope_api.infrastructure.db.session import DatabaseSettings, create_database_engine, session_factory
from launchscope_api.infrastructure.object_store import S3QuarantineObjectStore
from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_api.modules.evaluation.vertical_slice_application import VerticalSliceApplication
from launchscope_api.modules.identity_tenant.application import Actor, AuthorizationError

from .read_model import CursorInvalidError, ExperienceReadApplication

router = APIRouter(tags=["Experience", "Run events", "Ops audit"])


class ExecuteLocalDemoRequest(BaseModel):
    fixture_path: str = Field(min_length=1, max_length=500)


@lru_cache(maxsize=1)
def _from_env() -> ExperienceReadApplication:
    settings = DatabaseSettings.from_env()
    tenant_engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime")
    )
    ops_engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_OPS_DB_ROLE", "launchscope_ops")
    )
    return ExperienceReadApplication(session_factory(tenant_engine), ops_sessions=session_factory(ops_engine))


def get_read_model(request: Request) -> ExperienceReadApplication:
    configured = getattr(request.app.state, "experience_read_model", None)
    return configured if configured is not None else _from_env()


def get_vertical_slice(request: Request) -> VerticalSliceApplication:
    configured = getattr(request.app.state, "vertical_slice", None)
    if configured is not None:
        return configured
    if os.getenv("LAUNCHSCOPE_ENABLE_LOCAL_DEMO_EXECUTION", "").lower() != "true":
        raise HTTPException(status_code=404, detail="local demo execution is disabled")
    root = os.getenv("LAUNCHSCOPE_DEMO_FIXTURE_ROOT")
    if not root:
        raise HTTPException(status_code=503, detail="local demo fixture root is not configured")
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime")
    )
    return VerticalSliceApplication(session_factory(engine), S3QuarantineObjectStore.from_env(), Path(root))


def get_dispatch_application(request: Request) -> DispatchApplication:
    configured = getattr(request.app.state, "dispatch_application", None)
    if configured is not None:
        return configured
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime")
    )
    return DispatchApplication(session_factory(engine))


def get_object_store(request: Request) -> S3QuarantineObjectStore:
    configured = getattr(request.app.state, "object_store", None)
    return configured if configured is not None else S3QuarantineObjectStore.from_env()


def get_actor(
    x_tenant_id: UUID = Header(alias="X-Tenant-Id"),
    x_actor_id: str = Header(alias="X-Actor-Id", min_length=1, max_length=255),
) -> Actor:
    return Actor(tenant_id=x_tenant_id, actor_id=x_actor_id)


def _correlation_id(request: Request) -> str:
    return request.headers.get("X-Correlation-Id", "")


@router.get("/projects")
def list_projects(
    request: Request,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    return {
        "items": read_model.list_projects(actor, limit=limit),
        "next_cursor": None,
        "has_more": False,
        "correlation_id": _correlation_id(request),
    }


@router.get("/projects/{project_id}/runs")
def list_runs(
    project_id: UUID,
    request: Request,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    return {
        "items": read_model.list_runs(actor, project_id, limit=limit),
        "next_cursor": None,
        "has_more": False,
        "correlation_id": _correlation_id(request),
    }


@router.get("/runs/{run_id}")
def get_run(
    run_id: UUID, actor: Actor = Depends(get_actor), read_model: ExperienceReadApplication = Depends(get_read_model)
) -> dict[str, object]:
    return read_model.get_run(actor, run_id)


@router.post("/runs/{run_id}/execute-local-demo")
def execute_local_demo(
    run_id: UUID,
    body: ExecuteLocalDemoRequest,
    actor: Actor = Depends(get_actor),
    application: VerticalSliceApplication = Depends(get_vertical_slice),
) -> dict[str, object]:
    result = application.execute(actor, run_id, fixture_path=body.fixture_path)
    return {
        "run_id": str(result.run_id),
        "report_id": str(result.report_id),
        "status": result.status,
        "manifest_sha256": result.manifest_sha256,
        "evidence_ids": [str(value) for value in result.evidence_ids],
        "handoff_count": result.handoff_count,
        "tool_invocation_count": result.tool_invocation_count,
        "execution_mode": result.execution_mode,
    }


@router.post("/runs/{run_id}/dispatch", status_code=202)
def dispatch_run(
    run_id: UUID,
    request: Request,
    actor: Actor = Depends(get_actor),
    application: DispatchApplication = Depends(get_dispatch_application),
) -> dict[str, object]:
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key:
        raise ValueError("Idempotency-Key is required")
    result = application.dispatch(actor, run_id, idempotency_key=key)
    return {
        "run_id": str(result.run_id), "status": result.status,
        "manifest_sha256": result.manifest_sha256, "task_count": result.task_count,
        "execution_mode": "AGENTTEAMS_V1_2_ROCKETMQ",
    }


@router.get("/runs/{run_id}/events", response_model=None)
def stream_run_events(
    run_id: UUID,
    request: Request,
    cursor: str | None = Query(default=None, max_length=512),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID", max_length=512),
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
) -> StreamingResponse | JSONResponse:
    resume_cursor = last_event_id or cursor
    try:
        snapshot, events = read_model.run_events(actor, run_id, resume_cursor)
    except CursorInvalidError as exc:
        # This response is deliberate: callers must refetch the database
        # snapshot before retrying, never manufacture or silently reset a cursor.
        return JSONResponse(
            status_code=409,
            content={
                "error_code": "CURSOR_INVALID",
                "message": str(exc),
                "correlation_id": _correlation_id(request),
                "retryable": False,
                "details": {},
            },
        )

    def frames():
        yield "event: run.snapshot\n"
        yield f"id: {snapshot['current_cursor']}\n"
        yield f"data: {json.dumps(snapshot, separators=(',', ':'))}\n\n"
        for event in events:
            yield f"event: {event.event_type}\n"
            yield f"id: {event.cursor}\n"
            yield f"data: {json.dumps(event.data, separators=(',', ':'))}\n\n"

    return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/experience/runs/{run_id}/report")
def get_report(
    run_id: UUID, actor: Actor = Depends(get_actor), read_model: ExperienceReadApplication = Depends(get_read_model)
) -> dict[str, object]:
    return read_model.report(actor, run_id)


@router.get("/experience/runs/{run_id}/agentteams")
def get_agentteams_run(
    run_id: UUID, actor: Actor = Depends(get_actor), read_model: ExperienceReadApplication = Depends(get_read_model)
) -> dict[str, object]:
    return read_model.agentteams_run(actor, run_id)


@router.get("/experience/reports/{report_id}")
def get_report_by_id(
    report_id: UUID, actor: Actor = Depends(get_actor), read_model: ExperienceReadApplication = Depends(get_read_model)
) -> dict[str, object]:
    return read_model.report_by_id(actor, report_id)


@router.get("/experience/evidence/{evidence_id}/read-url")
def get_evidence_read_url(
    evidence_id: UUID,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
    object_store: S3QuarantineObjectStore = Depends(get_object_store),
) -> JSONResponse:
    object_key = read_model.evidence_object_key(actor, evidence_id)
    return JSONResponse(
        content={
            "evidence_id": str(evidence_id),
            "read_url": object_store.signed_read_url(object_key),
            "expires_in_seconds": object_store.settings.presign_ttl_seconds,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get("/experience/projects/{project_id}/compare/{run_id}")
def compare_runs(
    project_id: UUID,
    run_id: UUID,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
) -> dict[str, object]:
    return read_model.compare_runs(actor, project_id, run_id)


def require_ops_identity(x_ops_actor_id: str | None = Header(default=None, alias="X-Ops-Actor-Id")) -> str:
    allowed = {value.strip() for value in os.getenv("LAUNCHSCOPE_OPS_AUDIT_ACTORS", "").split(",") if value.strip()}
    if not x_ops_actor_id or x_ops_actor_id not in allowed:
        raise AuthorizationError("a separately authenticated Ops identity is required")
    return x_ops_actor_id


@router.get("/ops/audit/runs/{run_id}")
def ops_run(
    run_id: UUID,
    _ops_actor: str = Depends(require_ops_identity),
    read_model: ExperienceReadApplication = Depends(get_read_model),
) -> dict[str, object]:
    return read_model.ops_run(run_id)


@router.get("/ops/audit/events")
def ops_events(
    _ops_actor: str = Depends(require_ops_identity),
    read_model: ExperienceReadApplication = Depends(get_read_model),
    limit: int = Query(default=100, ge=1, le=100),
) -> dict[str, object]:
    return {"items": read_model.ops_events(limit=limit)}


__all__ = ["router"]
