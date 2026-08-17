# ruff: noqa: B008
"""T10 REST/SSE projection endpoints.

The frozen ``control-plane.v1`` transport fields are preserved.  Report,
comparison and Ops resources are documented separately as additive read-only
experience resources instead of changing the original contract in place.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from launchscope_api.infrastructure.db.session import DatabaseSettings, create_database_engine, session_factory
from launchscope_api.infrastructure.object_store import (
    ObjectStoreConfigurationError,
    ObjectStoreIntegrityError,
    S3QuarantineObjectStore,
)
from launchscope_api.modules.decision_report.export_application import (
    ExportRequest,
    ExportResult,
    ReportExportApplication,
    ReportExportBusyError,
    ReportExportIntegrityError,
)
from launchscope_api.modules.decision_report.export_renderer import PlaywrightReportRenderer
from launchscope_api.modules.evaluation.canonical_event_recovery import CanonicalEventRecoveryApplication
from launchscope_api.modules.evaluation.clarification_application import ClarificationApplication
from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_api.modules.evaluation.execution_control import ExecutionControlApplication
from launchscope_api.modules.evaluation.limit_amendment_application import RunLimitAmendmentApplication
from launchscope_api.modules.evaluation.vertical_slice_application import VerticalSliceApplication
from launchscope_api.modules.identity_tenant.application import Actor, AuthorizationError, NotFoundError
from launchscope_api.modules.user_validation.application import ArtifactIntegrityError, ReportTooLargeError
from launchscope_domain.value_objects import MAX_CLARIFICATION_ANSWER_CHARS

from .public_share import (
    PublicDemoShareApplication,
    PublicDemoShareGrant,
    PublicDemoShareResolver,
    PublicShareNotFound,
    PublicSharePublishError,
)
from .read_model import CursorInvalidError, ExperienceReadApplication

router = APIRouter(tags=["Experience", "Run events", "Ops audit"])


class ExecuteLocalDemoRequest(BaseModel):
    fixture_path: str = Field(min_length=1, max_length=500)


class ClarificationAnswerItem(BaseModel):
    request_id: UUID
    answer: str = Field(min_length=1, max_length=MAX_CLARIFICATION_ANSWER_CHARS)


class AnswerClarificationsRequest(BaseModel):
    answers: list[ClarificationAnswerItem] = Field(min_length=1, max_length=20)


class PauseRunRequest(BaseModel):
    expected_control_epoch: int = Field(ge=0)
    reason: str = Field(pattern="^USER_EXIT$")


class ResumeRunRequest(BaseModel):
    expected_control_epoch: int = Field(ge=0)


class RecoverRunRequest(BaseModel):
    expected_control_epoch: int = Field(ge=0)
    force: bool


class RunLimitAmendmentRequest(BaseModel):
    task_id: UUID
    matrix_event_id: str = Field(min_length=1, max_length=255)
    expected_control_epoch: int = Field(ge=0)
    expected_dispatch_epoch: int = Field(ge=0)
    expected_amendment_version: int = Field(ge=0)
    model_calls: int = Field(gt=0, le=4096)
    input_tokens: int = Field(gt=0, le=200_000_000)
    output_tokens: int = Field(gt=0, le=20_000_000)
    reason: str = Field(min_length=1, max_length=1000)


class CanonicalEventRecoveryRequest(BaseModel):
    task_id: UUID
    matrix_event_id: str = Field(min_length=1, max_length=255)
    expected_control_epoch: int = Field(ge=0)
    expected_dispatch_epoch: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)


class ReportExportRequest(BaseModel):
    kind: Literal["SUPERVISOR", "SPECIALIST", "PACKAGE"]
    agent_code: str | None = Field(default=None, max_length=120)
    view: Literal["SUMMARY", "FULL"] = "FULL"
    locale: str = Field(default="zh-CN", min_length=2, max_length=20)
    include_evidence: bool = False

    def to_domain(self) -> ExportRequest:
        return ExportRequest(
            kind=self.kind,
            agent_code=self.agent_code,
            view=self.view,
            locale=self.locale,
            include_evidence=self.include_evidence,
        )


class PublicExportRateLimiter:
    def __init__(self, *, limit: int = 20, window_seconds: int = 60) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, token: str) -> None:
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = time.monotonic()
        with self._lock:
            requests = self._requests[key]
            while requests and requests[0] <= now - self._window_seconds:
                requests.popleft()
            if len(requests) >= self._limit:
                raise HTTPException(status_code=429, detail="public report export rate limit exceeded")
            requests.append(now)


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
    return DispatchApplication(session_factory(engine), get_object_store(request))


def get_clarification_application(request: Request) -> ClarificationApplication:
    configured = getattr(request.app.state, "clarification_application", None)
    if configured is not None:
        return configured
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime")
    )
    try:
        objects: S3QuarantineObjectStore | None = get_object_store(request)
    except Exception:  # pragma: no cover - object store is optional for reads
        objects = None
    return ClarificationApplication(session_factory(engine), objects)


def get_execution_control_application(request: Request) -> ExecutionControlApplication:
    configured = getattr(request.app.state, "execution_control_application", None)
    if configured is not None:
        return configured
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime")
    )
    return ExecutionControlApplication(session_factory(engine))


def get_run_limit_amendment_application(request: Request) -> RunLimitAmendmentApplication:
    configured = getattr(request.app.state, "run_limit_amendment_application", None)
    if configured is not None:
        return configured
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime")
    )
    return RunLimitAmendmentApplication(session_factory(engine))


def get_canonical_event_recovery_application(request: Request) -> CanonicalEventRecoveryApplication:
    configured = getattr(request.app.state, "canonical_event_recovery_application", None)
    if configured is not None:
        return configured
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime")
    )
    return CanonicalEventRecoveryApplication(session_factory(engine))


def get_object_store(request: Request) -> S3QuarantineObjectStore:
    configured = getattr(request.app.state, "object_store", None)
    return configured if configured is not None else S3QuarantineObjectStore.from_env()


@lru_cache(maxsize=1)
def _export_sessions_from_env():
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime")
    )
    return session_factory(engine)


def get_report_export_application(request: Request) -> ReportExportApplication:
    configured = getattr(request.app.state, "report_export_application", None)
    if configured is not None:
        return configured
    renderer = PlaywrightReportRenderer(os.getenv("LAUNCHSCOPE_REPORT_RENDER_WEB_URL", "http://127.0.0.1:3000"))
    return ReportExportApplication(_export_sessions_from_env(), get_object_store(request), renderer)


_public_export_limiter = PublicExportRateLimiter()


def get_public_export_limiter(request: Request) -> PublicExportRateLimiter:
    configured = getattr(request.app.state, "public_export_rate_limiter", None)
    return configured if configured is not None else _public_export_limiter


@lru_cache(maxsize=1)
def _public_share_from_env() -> PublicDemoShareResolver:
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime")
    )
    return PublicDemoShareResolver(session_factory(engine))


def get_public_share_resolver(request: Request) -> PublicDemoShareResolver:
    configured = getattr(request.app.state, "public_share_resolver", None)
    return configured if configured is not None else _public_share_from_env()


def get_public_share_application(request: Request) -> PublicDemoShareApplication:
    configured = getattr(request.app.state, "public_share_application", None)
    if configured is not None:
        return configured
    return _public_share_application_from_env()


@lru_cache(maxsize=1)
def _public_share_application_from_env() -> PublicDemoShareApplication:
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime")
    )
    return PublicDemoShareApplication(session_factory(engine))


@lru_cache(maxsize=4)
def _report_schema(kind: str, version: str = "2.0") -> dict[str, object]:
    stem = "supervisor-report" if kind == "SUPERVISOR" else "specialist-report"
    filename = f"{stem}.v{version.split('.', 1)[0]}.json"
    root = Path(__file__).resolve().parents[6]
    return json.loads((root / "packages/contracts/reports" / filename).read_text(encoding="utf-8"))


def _load_canonical_report(
    metadata: dict[str, object],
    *,
    kind: str,
    version: str,
    object_store: S3QuarantineObjectStore,
) -> dict[str, object]:
    object_key = str(metadata["object_key"])
    expected_sha256 = str(metadata["sha256"])
    try:
        observed = object_store.head(object_key)
        if observed is None or observed.sha256 != expected_sha256:
            raise ArtifactIntegrityError("report object does not match the durable catalog")
        if observed.size_bytes > 2_000_000:
            raise ReportTooLargeError("report exceeds the 2 MB read limit")
        body = object_store.get_private(object_key, max_bytes=2_000_000)
    except ObjectStoreIntegrityError as exc:
        raise ArtifactIntegrityError("report object failed immutable integrity validation") from exc
    except ObjectStoreConfigurationError as exc:
        raise HTTPException(status_code=503, detail="report object store is unavailable") from exc
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise ArtifactIntegrityError("report body does not match the durable catalog")
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactIntegrityError("report object is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise ArtifactIntegrityError("report object is not a JSON document")
    errors = sorted(
        Draft202012Validator(_report_schema(kind, version), format_checker=FormatChecker()).iter_errors(document),
        key=lambda item: item.json_path,
    )
    if errors:
        raise ArtifactIntegrityError(f"report object violates its immutable contract at {errors[0].json_path}")
    if document["report_id"] != metadata["report_id"] or document["run_id"] != metadata["run_id"]:
        raise ArtifactIntegrityError("report object identity does not match the durable catalog")
    if kind == "SPECIALIST" and document["agent_code"] != metadata["agent_code"]:
        raise ArtifactIntegrityError("specialist report identity does not match the durable catalog")
    projection: dict[str, object] = {"view": "FULL", "created_at": metadata["created_at"]}
    if metadata.get("supervisor_report_id"):
        projection["supervisor_report_id"] = metadata["supervisor_report_id"]
    return {
        "report_schema_version": version,
        "document": document,
        "integrity": {
            "canonical_sha256": expected_sha256,
            "source_sha256": document["source_sha256"],
        },
        "projection": projection,
    }


def _load_report_v2(
    metadata: dict[str, object],
    *,
    kind: str,
    object_store: S3QuarantineObjectStore,
) -> dict[str, object]:
    return _load_canonical_report(metadata, kind=kind, version="2.0", object_store=object_store)


def _load_report_v3(
    metadata: dict[str, object],
    *,
    kind: str,
    object_store: S3QuarantineObjectStore,
) -> dict[str, object]:
    return _load_canonical_report(metadata, kind=kind, version="3.0", object_store=object_store)


def _public_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="shared Demo resource was not found")


def get_actor(
    x_tenant_id: UUID = Header(alias="X-Tenant-Id"),
    x_actor_id: str = Header(alias="X-Actor-Id", min_length=1, max_length=255),
) -> Actor:
    return Actor(tenant_id=x_tenant_id, actor_id=x_actor_id)


def _correlation_id(request: Request) -> str:
    return request.headers.get("X-Correlation-Id", "")


def _public_share_actor(resource_id: UUID, token: str) -> Actor:
    expected = os.getenv("LAUNCHSCOPE_PUBLIC_DEMO_SHARE_TOKEN", "").strip()
    tenant_id = os.getenv("LAUNCHSCOPE_PUBLIC_DEMO_TENANT_ID", "").strip()
    actor_id = os.getenv("LAUNCHSCOPE_PUBLIC_DEMO_ACTOR_ID", "").strip()
    allowed_ids = {
        value.strip() for value in os.getenv("LAUNCHSCOPE_PUBLIC_DEMO_RESOURCE_IDS", "").split(",") if value.strip()
    }
    if (
        not expected
        or not tenant_id
        or not actor_id
        or str(resource_id) not in allowed_ids
        or not hmac.compare_digest(expected, token)
    ):
        raise HTTPException(status_code=404, detail="shared Demo resource was not found")
    try:
        return Actor(tenant_id=UUID(tenant_id), actor_id=actor_id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="public Demo sharing is misconfigured") from exc


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


@router.get("/projects/{project_id}/portrait")
def get_project_portrait(
    project_id: UUID,
    request: Request,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
) -> dict[str, object]:
    return {**read_model.project_portrait(actor, project_id), "correlation_id": _correlation_id(request)}


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


@router.get("/experience/history")
def evaluation_history(
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    search: str = Query(default="", max_length=200),
    sort: str = Query(default="newest", pattern="^(newest|oldest)$"),
) -> dict[str, object]:
    return read_model.evaluation_history(actor, limit=limit, offset=offset, search=search, sort=sort)


@router.get("/runs/{run_id}")
def get_run(
    run_id: UUID, actor: Actor = Depends(get_actor), read_model: ExperienceReadApplication = Depends(get_read_model)
) -> dict[str, object]:
    return read_model.get_run(actor, run_id)


@router.get("/public/demo/runs/{run_id}")
def get_public_demo_run(
    run_id: UUID,
    token: str = Query(min_length=32, max_length=256),
    read_model: ExperienceReadApplication = Depends(get_read_model),
) -> dict[str, object]:
    actor = _public_share_actor(run_id, token)
    projection = read_model.get_run(actor, run_id)
    if projection["status"] != "COMPLETED":
        raise HTTPException(status_code=404, detail="shared Demo resource was not found")
    return projection


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
        "run_id": str(result.run_id),
        "status": result.status,
        "manifest_sha256": result.manifest_sha256,
        "task_count": result.task_count,
        "execution_mode": "AGENTTEAMS_V1_2_ROCKETMQ",
    }


@router.get("/runs/{run_id}/execution-control")
def get_run_execution_control(
    run_id: UUID,
    actor: Actor = Depends(get_actor),
    application: ExecutionControlApplication = Depends(get_execution_control_application),
) -> dict[str, object]:
    return application.get(actor, run_id).to_dict()


@router.post("/runs/{run_id}/pause", status_code=202)
def pause_run(
    run_id: UUID,
    payload: PauseRunRequest,
    request: Request,
    actor: Actor = Depends(get_actor),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    correlation_id: UUID = Header(alias="X-Correlation-Id"),
    application: ExecutionControlApplication = Depends(get_execution_control_application),
) -> dict[str, object]:
    return application.pause(
        actor,
        run_id,
        expected_control_epoch=payload.expected_control_epoch,
        reason=payload.reason,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    ).to_dict()


@router.post("/runs/{run_id}/resume", status_code=202)
def resume_run(
    run_id: UUID,
    payload: ResumeRunRequest,
    request: Request,
    actor: Actor = Depends(get_actor),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    correlation_id: UUID = Header(alias="X-Correlation-Id"),
    application: ExecutionControlApplication = Depends(get_execution_control_application),
) -> dict[str, object]:
    return application.resume(
        actor,
        run_id,
        expected_control_epoch=payload.expected_control_epoch,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    ).to_dict()


@router.post("/runs/{run_id}/recover", status_code=202)
def recover_run(
    run_id: UUID,
    payload: RecoverRunRequest,
    actor: Actor = Depends(get_actor),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    correlation_id: UUID = Header(alias="X-Correlation-Id"),
    application: ExecutionControlApplication = Depends(get_execution_control_application),
) -> dict[str, object]:
    return application.recover(
        actor,
        run_id,
        expected_control_epoch=payload.expected_control_epoch,
        force=payload.force,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    ).to_dict()


@router.post("/runs/{run_id}/limit-amendments", status_code=202)
def amend_run_limits(
    run_id: UUID,
    payload: RunLimitAmendmentRequest,
    actor: Actor = Depends(get_actor),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    correlation_id: UUID = Header(alias="X-Correlation-Id"),
    application: RunLimitAmendmentApplication = Depends(get_run_limit_amendment_application),
) -> dict[str, object]:
    return application.amend(
        actor,
        run_id,
        task_id=payload.task_id,
        matrix_event_id=payload.matrix_event_id,
        expected_control_epoch=payload.expected_control_epoch,
        expected_dispatch_epoch=payload.expected_dispatch_epoch,
        expected_amendment_version=payload.expected_amendment_version,
        model_calls=payload.model_calls,
        input_tokens=payload.input_tokens,
        output_tokens=payload.output_tokens,
        reason=payload.reason,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    ).to_dict()


@router.post("/runs/{run_id}/canonical-event-recoveries", status_code=202)
def recover_canonical_event(
    run_id: UUID,
    payload: CanonicalEventRecoveryRequest,
    actor: Actor = Depends(get_actor),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    correlation_id: UUID = Header(alias="X-Correlation-Id"),
    application: CanonicalEventRecoveryApplication = Depends(get_canonical_event_recovery_application),
) -> dict[str, object]:
    return application.recover(
        actor,
        run_id,
        task_id=payload.task_id,
        matrix_event_id=payload.matrix_event_id,
        expected_control_epoch=payload.expected_control_epoch,
        expected_dispatch_epoch=payload.expected_dispatch_epoch,
        reason=payload.reason,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    ).to_dict()


@router.get("/runs/{run_id}/clarifications")
def list_clarifications(
    run_id: UUID,
    request: Request,
    actor: Actor = Depends(get_actor),
    application: ClarificationApplication = Depends(get_clarification_application),
) -> dict[str, object]:
    """Open Agent-initiated questions, rendered as the right-hand quest prompts."""

    questions = application.open_questions(actor, run_id)
    return {
        "run_id": str(run_id),
        "items": [
            {
                "request_id": str(item.request_id),
                "task_id": str(item.task_id),
                "agent_code": item.agent_code,
                "field": item.profile_field,
                "question": item.question,
                "why_blocking": item.why_blocking,
                "impact_dimension": item.impact_dimension,
            }
            for item in questions
        ],
        "correlation_id": _correlation_id(request),
    }


@router.post("/runs/{run_id}/clarifications:answer")
def answer_clarifications(
    run_id: UUID,
    payload: AnswerClarificationsRequest,
    request: Request,
    actor: Actor = Depends(get_actor),
    application: ClarificationApplication = Depends(get_clarification_application),
) -> dict[str, object]:
    """Commit answers to the ProductProfile, then resume only the affected Tasks."""

    answers = {item.request_id: item.answer for item in payload.answers}
    if len(answers) != len(payload.answers):
        raise ValueError("each information request may be answered at most once per call")
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key:
        raise ValueError("Idempotency-Key is required")
    result = application.answer(
        actor,
        run_id,
        answers,
        correlation_id=_correlation_id(request),
        idempotency_key=key,
    )
    return {
        "run_id": str(run_id),
        "run_status": result.run_status,
        "affected_task_ids": [str(value) for value in result.affected_task_ids],
        "unaffected_task_ids": [str(value) for value in result.unaffected_task_ids],
        "dispatched": result.dispatched,
        "correlation_id": _correlation_id(request),
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


@router.get("/experience/v2/runs/{run_id}/report")
def get_report_v2(
    run_id: UUID,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
    object_store: S3QuarantineObjectStore = Depends(get_object_store),
) -> JSONResponse:
    payload = _load_report_v2(
        read_model.report_v2_metadata(actor, run_id),
        kind="SUPERVISOR",
        object_store=object_store,
    )
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/experience/v3/runs/{run_id}/report")
def get_report_v3(
    run_id: UUID,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
    object_store: S3QuarantineObjectStore = Depends(get_object_store),
) -> JSONResponse:
    payload = _load_report_v3(
        read_model.report_v3_metadata(actor, run_id),
        kind="SUPERVISOR",
        object_store=object_store,
    )
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/experience/runs/{run_id}/agentteams")
def get_agentteams_run(
    run_id: UUID, actor: Actor = Depends(get_actor), read_model: ExperienceReadApplication = Depends(get_read_model)
) -> dict[str, object]:
    return read_model.agentteams_run(actor, run_id)


@router.get("/experience/runs/{run_id}/agent-reports")
def get_agent_report_summaries(
    run_id: UUID,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
) -> dict[str, object]:
    return read_model.agent_report_summaries(actor, run_id)


@router.get("/experience/v2/runs/{run_id}/agent-reports")
def get_agent_report_summaries_v2(
    run_id: UUID,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
) -> dict[str, object]:
    return read_model.agent_report_summaries_v2(actor, run_id)


@router.get("/experience/v3/runs/{run_id}/agent-reports")
def get_agent_report_summaries_v3(
    run_id: UUID,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
) -> dict[str, object]:
    return read_model.agent_report_summaries_v3(actor, run_id)


@router.get("/experience/v2/runs/{run_id}/agent-reports/{agent_code}")
def get_agent_report_v2(
    run_id: UUID,
    agent_code: str,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
    object_store: S3QuarantineObjectStore = Depends(get_object_store),
) -> JSONResponse:
    payload = _load_report_v2(
        read_model.agent_report_metadata_v2(actor, run_id, agent_code),
        kind="SPECIALIST",
        object_store=object_store,
    )
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/experience/v3/runs/{run_id}/agent-reports/{agent_code}")
def get_agent_report_v3(
    run_id: UUID,
    agent_code: str,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
    object_store: S3QuarantineObjectStore = Depends(get_object_store),
) -> JSONResponse:
    payload = _load_report_v3(
        read_model.agent_report_metadata_v3(actor, run_id, agent_code),
        kind="SPECIALIST",
        object_store=object_store,
    )
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/experience/runs/{run_id}/agent-reports/{agent_code}")
def get_agent_report(
    run_id: UUID,
    agent_code: str,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
    object_store: S3QuarantineObjectStore = Depends(get_object_store),
) -> JSONResponse:
    metadata = read_model.agent_report_metadata(actor, run_id, agent_code)
    object_key = str(metadata.pop("object_key"))
    try:
        observed = object_store.head(object_key)
        if observed is None or observed.sha256 != metadata["sha256"]:
            raise ArtifactIntegrityError("Agent report object does not match durable catalog metadata")
        if observed.size_bytes > 2_000_000:
            raise ReportTooLargeError("Agent report exceeds the 2 MB read limit")
        body = object_store.get_private(object_key, max_bytes=2_000_000)
    except ObjectStoreIntegrityError as exc:
        raise ArtifactIntegrityError("Agent report object failed immutable integrity validation") from exc
    except ObjectStoreConfigurationError as exc:
        raise HTTPException(status_code=503, detail="Agent report object store is unavailable") from exc
    if hashlib.sha256(body).hexdigest() != metadata["sha256"]:
        raise ArtifactIntegrityError("Agent report body does not match durable catalog metadata")
    mime_type = str(metadata["mime_type"]).split(";", 1)[0].lower()
    projected = False
    if metadata["kind"] == "DOMAIN":
        try:
            content = body.decode("utf-8")
            parsed = json.loads(content) if mime_type == "application/json" else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        if not isinstance(parsed, dict) or parsed.get("schema_version") != "DomainAgentReportViewV1":
            projection = read_model.domain_agent_report_projection(actor, run_id, agent_code)
            content = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            mime_type = "application/json"
            projected = True
    else:
        try:
            content = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactIntegrityError("Agent report is not valid UTF-8 text") from exc
    report_format = (
        "json"
        if mime_type == "application/json"
        else "markdown"
        if mime_type in {"text/markdown", "text/x-markdown"}
        else "text"
    )
    return JSONResponse(
        content={
            **metadata,
            "format": report_format,
            "content": content,
            "projection_status": "LEGACY_SOURCE_PROJECTED" if projected else "ORIGINAL_ARTIFACT",
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get("/public/demo/runs/{run_id}/agentteams")
def get_public_demo_agentteams_run(
    run_id: UUID,
    token: str = Query(min_length=32, max_length=256),
    read_model: ExperienceReadApplication = Depends(get_read_model),
) -> dict[str, object]:
    actor = _public_share_actor(run_id, token)
    run = read_model.get_run(actor, run_id)
    if run["status"] != "COMPLETED":
        raise HTTPException(status_code=404, detail="shared Demo resource was not found")
    return read_model.agentteams_run(actor, run_id)


@router.get("/experience/reports/{report_id}")
def get_report_by_id(
    report_id: UUID, actor: Actor = Depends(get_actor), read_model: ExperienceReadApplication = Depends(get_read_model)
) -> dict[str, object]:
    return read_model.report_by_id(actor, report_id)


@router.get("/experience/v2/reports/{report_id}")
def get_report_v2_by_id(
    report_id: UUID,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
    object_store: S3QuarantineObjectStore = Depends(get_object_store),
) -> JSONResponse:
    payload = _load_report_v2(
        read_model.report_v2_metadata_by_id(actor, report_id),
        kind="SUPERVISOR",
        object_store=object_store,
    )
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/experience/v3/reports/{report_id}")
def get_report_v3_by_id(
    report_id: UUID,
    actor: Actor = Depends(get_actor),
    read_model: ExperienceReadApplication = Depends(get_read_model),
    object_store: S3QuarantineObjectStore = Depends(get_object_store),
) -> JSONResponse:
    payload = _load_report_v3(
        read_model.report_v3_metadata_by_id(actor, report_id),
        kind="SUPERVISOR",
        object_store=object_store,
    )
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.post("/experience/v2/reports/{report_id}/public-demo-share", status_code=201)
def publish_public_demo_share(
    report_id: UUID,
    actor: Actor = Depends(get_actor),
    application: PublicDemoShareApplication = Depends(get_public_share_application),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    x_correlation_id: str = Header(alias="X-Correlation-Id", min_length=1, max_length=255),
) -> dict[str, object]:
    del x_correlation_id
    try:
        return application.publish(actor, report_id, idempotency_key=idempotency_key)
    except PublicSharePublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/experience/reports/{report_id}/exports", status_code=201)
def create_report_export(
    report_id: UUID,
    body: ReportExportRequest,
    actor: Actor = Depends(get_actor),
    application: ReportExportApplication = Depends(get_report_export_application),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    x_correlation_id: str = Header(alias="X-Correlation-Id", min_length=1, max_length=255),
) -> dict[str, object]:
    del x_correlation_id
    result = application.create(actor, report_id, body.to_domain(), idempotency_key=idempotency_key)
    return result.as_dict()


@router.get("/experience/report-exports/{export_id}")
def get_report_export(
    export_id: UUID,
    actor: Actor = Depends(get_actor),
    application: ReportExportApplication = Depends(get_report_export_application),
) -> dict[str, object]:
    return application.get(actor, export_id).as_dict()


@router.get("/experience/report-exports/{export_id}/read-url")
def get_report_export_read_url(
    export_id: UUID,
    actor: Actor = Depends(get_actor),
    application: ReportExportApplication = Depends(get_report_export_application),
) -> dict[str, object]:
    try:
        return application.read_url(actor, export_id)
    except ReportExportBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ReportExportIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/public/demo/reports/{report_id}")
def get_public_demo_report_by_id(
    report_id: UUID,
    token: str = Query(min_length=32, max_length=256),
    read_model: ExperienceReadApplication = Depends(get_read_model),
) -> dict[str, object]:
    actor = _public_share_actor(report_id, token)
    projection = read_model.report_by_id(actor, report_id)
    if projection.get("layered_report") is None:
        raise HTTPException(status_code=404, detail="shared Demo resource was not found")
    return projection


@router.get("/public/demo/v2/reports/{report_id}")
def get_public_demo_report_v2(
    report_id: UUID,
    token: str = Query(min_length=32, max_length=256),
    resolver: PublicDemoShareResolver = Depends(get_public_share_resolver),
    object_store: S3QuarantineObjectStore = Depends(get_object_store),
) -> JSONResponse:
    try:
        grant = resolver.resolve(token)
        metadata = resolver.supervisor_metadata(grant, report_id)
    except PublicShareNotFound as exc:
        raise _public_not_found() from exc
    payload = _load_report_v2(metadata, kind="SUPERVISOR", object_store=object_store)
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/public/demo/v3/reports/{report_id}")
def get_public_demo_report_v3(
    report_id: UUID,
    token: str = Query(min_length=32, max_length=256),
    resolver: PublicDemoShareResolver = Depends(get_public_share_resolver),
    object_store: S3QuarantineObjectStore = Depends(get_object_store),
) -> JSONResponse:
    try:
        grant = resolver.resolve(token)
        metadata = resolver.supervisor_metadata(grant, report_id)
    except PublicShareNotFound as exc:
        raise _public_not_found() from exc
    payload = _load_report_v3(metadata, kind="SUPERVISOR", object_store=object_store)
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _public_export_actor(grant: PublicDemoShareGrant) -> Actor:
    return Actor(grant.tenant_id, f"public-demo-share:{grant.share_id}")


def _authorize_public_export(grant: PublicDemoShareGrant, result: ExportResult) -> None:
    if result.report_id != grant.report_id or result.run_id != grant.run_id:
        raise _public_not_found()
    if result.kind == "SPECIALIST" and not grant.include_agent_reports:
        raise _public_not_found()
    if result.kind == "PACKAGE" and not grant.include_agent_reports:
        raise _public_not_found()
    if result.include_evidence and not grant.include_evidence:
        raise _public_not_found()


@router.post("/public/demo/v2/reports/{report_id}/exports", status_code=201)
def create_public_demo_report_export(
    report_id: UUID,
    body: ReportExportRequest,
    token: str = Query(min_length=32, max_length=256),
    resolver: PublicDemoShareResolver = Depends(get_public_share_resolver),
    application: ReportExportApplication = Depends(get_report_export_application),
    limiter: PublicExportRateLimiter = Depends(get_public_export_limiter),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    x_correlation_id: str = Header(alias="X-Correlation-Id", min_length=1, max_length=255),
) -> dict[str, object]:
    del x_correlation_id
    limiter.check(token)
    try:
        grant = resolver.resolve(token)
    except PublicShareNotFound as exc:
        raise _public_not_found() from exc
    if report_id != grant.report_id:
        raise _public_not_found()
    if (body.kind in {"SPECIALIST", "PACKAGE"} and not grant.include_agent_reports) or (
        body.include_evidence and not grant.include_evidence
    ):
        raise _public_not_found()
    try:
        result = application.create(
            _public_export_actor(grant), report_id, body.to_domain(), idempotency_key=idempotency_key
        )
    except NotFoundError as exc:
        raise _public_not_found() from exc
    _authorize_public_export(grant, result)
    return result.as_dict()


@router.get("/public/demo/v2/report-exports/{export_id}")
def get_public_demo_report_export(
    export_id: UUID,
    token: str = Query(min_length=32, max_length=256),
    resolver: PublicDemoShareResolver = Depends(get_public_share_resolver),
    application: ReportExportApplication = Depends(get_report_export_application),
    limiter: PublicExportRateLimiter = Depends(get_public_export_limiter),
) -> dict[str, object]:
    limiter.check(token)
    try:
        grant = resolver.resolve(token)
    except PublicShareNotFound as exc:
        raise _public_not_found() from exc
    try:
        result = application.get(_public_export_actor(grant), export_id)
    except NotFoundError as exc:
        raise _public_not_found() from exc
    _authorize_public_export(grant, result)
    return result.as_dict()


@router.get("/public/demo/v2/report-exports/{export_id}/read-url")
def get_public_demo_report_export_read_url(
    export_id: UUID,
    token: str = Query(min_length=32, max_length=256),
    resolver: PublicDemoShareResolver = Depends(get_public_share_resolver),
    application: ReportExportApplication = Depends(get_report_export_application),
    limiter: PublicExportRateLimiter = Depends(get_public_export_limiter),
) -> dict[str, object]:
    limiter.check(token)
    try:
        grant = resolver.resolve(token)
    except PublicShareNotFound as exc:
        raise _public_not_found() from exc
    actor = _public_export_actor(grant)
    try:
        result = application.get(actor, export_id)
    except NotFoundError as exc:
        raise _public_not_found() from exc
    _authorize_public_export(grant, result)
    try:
        return application.read_url(actor, export_id)
    except (ReportExportBusyError, ReportExportIntegrityError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/public/demo/v2/agent-reports/{agent_code}")
def get_public_demo_agent_report_v2(
    agent_code: str,
    token: str = Query(min_length=32, max_length=256),
    resolver: PublicDemoShareResolver = Depends(get_public_share_resolver),
    object_store: S3QuarantineObjectStore = Depends(get_object_store),
) -> JSONResponse:
    try:
        grant = resolver.resolve(token)
        metadata = resolver.agent_metadata(grant, agent_code)
    except PublicShareNotFound as exc:
        raise _public_not_found() from exc
    payload = _load_report_v2(metadata, kind="SPECIALIST", object_store=object_store)
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/public/demo/v3/agent-reports/{agent_code}")
def get_public_demo_agent_report_v3(
    agent_code: str,
    token: str = Query(min_length=32, max_length=256),
    resolver: PublicDemoShareResolver = Depends(get_public_share_resolver),
    object_store: S3QuarantineObjectStore = Depends(get_object_store),
) -> JSONResponse:
    try:
        grant = resolver.resolve(token)
        metadata = resolver.agent_metadata(grant, agent_code)
    except PublicShareNotFound as exc:
        raise _public_not_found() from exc
    payload = _load_report_v3(metadata, kind="SPECIALIST", object_store=object_store)
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/public/demo/v2/evidence/{evidence_id}/read-url")
def get_public_demo_evidence_read_url_v2(
    evidence_id: UUID,
    token: str = Query(min_length=32, max_length=256),
    resolver: PublicDemoShareResolver = Depends(get_public_share_resolver),
    object_store: S3QuarantineObjectStore = Depends(get_object_store),
) -> JSONResponse:
    try:
        grant = resolver.resolve(token)
        metadata = resolver.evidence_metadata(grant, evidence_id)
    except PublicShareNotFound as exc:
        raise _public_not_found() from exc
    observed = object_store.head(str(metadata["object_key"]))
    if observed is None or observed.sha256 != metadata["sha256"]:
        raise ArtifactIntegrityError("evidence object does not match the durable catalog")
    return JSONResponse(
        content={
            "evidence_id": str(evidence_id),
            "run_id": metadata["run_id"],
            "sha256": metadata["sha256"],
            "mime_type": metadata["mime_type"],
            "read_url": object_store.signed_read_url(str(metadata["object_key"])),
            "expires_in_seconds": object_store.settings.presign_ttl_seconds,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


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
