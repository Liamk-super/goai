# ruff: noqa: B008
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from launchscope_api.main import request_actor
from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.project_dossier.model_extraction import IntakeModelExtractor

from .conversation_application import CONVERSATION_CHANNELS, RunConversationApplication
from .intake_application import SupervisorChatApplication

router = APIRouter(tags=["Supervisor chat"])


class SupervisorMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=30_000)
    allow_external_processing: bool


class RunConversationMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=30_000)
    allow_external_processing: bool


def get_supervisor(request: Request) -> SupervisorChatApplication:
    control_plane = getattr(request.app.state, "control_plane", None)
    if control_plane is None:
        from launchscope_api.main import get_control_plane

        control_plane = get_control_plane()
    supervisor = getattr(control_plane, "supervisor", None)
    if not isinstance(supervisor, SupervisorChatApplication):
        raise RuntimeError("the persistent supervisor application is unavailable")
    return supervisor


def get_run_conversations(request: Request) -> RunConversationApplication:
    control_plane = getattr(request.app.state, "control_plane", None)
    if control_plane is None:
        from launchscope_api.main import get_control_plane

        control_plane = get_control_plane()
    application = getattr(control_plane, "run_conversations", None)
    if not isinstance(application, RunConversationApplication):
        raise RuntimeError("the persistent Run conversation application is unavailable")
    return application


@router.post("/projects/{project_id}/versions/{version_id}/supervisor/messages", status_code=202)
def submit_supervisor_message(
    project_id: UUID,
    version_id: UUID,
    body: SupervisorMessageRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    correlation_id: UUID = Header(alias="X-Correlation-Id"),
    actor: Actor = Depends(request_actor),
    supervisor: SupervisorChatApplication = Depends(get_supervisor),
) -> dict[str, object]:
    proposal = IntakeModelExtractor().extract_requirement(
        body.message,
        allow_external_processing=body.allow_external_processing,
    )
    result = supervisor.submit_requirement(
        actor,
        project_id,
        version_id,
        message=body.message,
        model_output=proposal,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )
    return {
        "message_id": result.message_id,
        "brief_id": result.brief_id,
        "brief_revision": result.brief_revision,
        "interaction_state": result.interaction_state,
        "confirmation_required": result.confirmation_required,
        "questions": result.questions,
        "duplicate": result.duplicate,
    }


@router.get("/runs/{run_id}/conversations")
def list_run_conversations(
    run_id: UUID,
    cursor: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    actor: Actor = Depends(request_actor),
    application: RunConversationApplication = Depends(get_run_conversations),
) -> dict[str, object]:
    return application.list_conversations(actor, run_id, cursor=cursor, limit=limit)


@router.post("/runs/{run_id}/conversations/{channel}/messages", status_code=202)
def submit_run_conversation_message(
    run_id: UUID,
    channel: str,
    body: RunConversationMessageRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    correlation_id: UUID = Header(alias="X-Correlation-Id"),
    actor: Actor = Depends(request_actor),
    application: RunConversationApplication = Depends(get_run_conversations),
) -> dict[str, object]:
    normalized_channel = channel.strip().lower()
    if normalized_channel not in CONVERSATION_CHANNELS:
        raise ValueError("unsupported Run conversation channel")
    proposal = None
    if normalized_channel == "supervisor":
        proposal = IntakeModelExtractor().extract_requirement(
            body.message,
            allow_external_processing=body.allow_external_processing,
        )
    result = application.submit(
        actor,
        run_id,
        normalized_channel,
        message=body.message,
        allow_external_processing=body.allow_external_processing,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        supervisor_model_output=proposal,
    )
    return {
        "message_id": str(result.message_id),
        "run_id": str(result.run_id),
        "channel": result.channel,
        "route_state": result.route_state,
        "affected_task_ids": [str(value) for value in result.affected_task_ids],
        "questions": list(result.questions),
        "duplicate": result.duplicate,
    }
