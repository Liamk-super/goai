# ruff: noqa: B008
"""T5 REST endpoints for project intake and confirmation."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from launchscope_api.modules.identity_tenant.application import Actor

from .application import ProjectDossierApplication
from .model_extraction import IntakeModelExtractor
from .persistent_application import PersistentProjectDossierApplication

router = APIRouter(tags=["Projects", "Product versions", "Materials", "Intake"])
logger = logging.getLogger(__name__)


class CreateProjectRequest(BaseModel):
    workspace_id: UUID
    name: str = Field(min_length=1, max_length=200)


class CreateVersionRequest(BaseModel):
    label: str = Field(min_length=1, max_length=100)


class InitiateMaterialRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    mime_type: str = Field(min_length=1, max_length=255)


class AnswerGapsRequest(BaseModel):
    correlation_id: UUID
    answers: dict[str, str]


class ConfirmProfileRequest(BaseModel):
    acknowledge_model_inference: bool


class ExtractIntakeRequest(BaseModel):
    raw_content: str = Field(min_length=1, max_length=30_000)
    allow_external_processing: bool


def get_dossier(request: Request) -> ProjectDossierApplication | PersistentProjectDossierApplication:
    configured = getattr(request.app.state, "control_plane", None)
    if configured is not None:
        return configured.dossier
    from launchscope_api.main import get_control_plane

    return get_control_plane().dossier


def get_actor(
    x_tenant_id: UUID = Header(alias="X-Tenant-Id"),
    x_actor_id: str = Header(alias="X-Actor-Id", min_length=1, max_length=255),
) -> Actor:
    return Actor(tenant_id=x_tenant_id, actor_id=x_actor_id)


def correlation_id(x_correlation_id: UUID = Header(alias="X-Correlation-Id")) -> UUID:
    return x_correlation_id


@router.post("/intake:extract")
def extract_intake(request: ExtractIntakeRequest, _actor: Actor = Depends(get_actor)) -> dict[str, object]:
    """Return a model-inferred draft; this endpoint deliberately persists no fact."""
    try:
        draft = IntakeModelExtractor().extract(
            request.raw_content,
            allow_external_processing=request.allow_external_processing,
        )
    except Exception:
        logger.warning(
            "intake model extraction failed",
            extra={"tenant_id": str(_actor.tenant_id), "actor_id": _actor.actor_id},
        )
        raise
    logger.info(
        "intake model extraction completed",
        extra={
            "tenant_id": str(_actor.tenant_id),
            "actor_id": _actor.actor_id,
            "model_id": draft.model_id,
            "missing_field_count": len(draft.missing_fields),
        },
    )
    return {
        "source": "MODEL_INFERENCE",
        "model_id": draft.model_id,
        "extracted_fields": draft.fields,
        "missing_fields": draft.missing_fields,
        "confirmation_required": True,
    }


@router.post("/projects", status_code=201)
def create_project(
    request: CreateProjectRequest,
    actor: Actor = Depends(get_actor),
    dossier: ProjectDossierApplication = Depends(get_dossier),
) -> dict[str, str]:
    project = dossier.create_project(actor, request.workspace_id, request.name)
    return {"project_id": str(project.project_id), "workspace_id": str(project.workspace_id), "name": project.name}


@router.post("/projects/{project_id}/versions", status_code=201)
def create_version(
    project_id: UUID,
    request: CreateVersionRequest,
    actor: Actor = Depends(get_actor),
    dossier: ProjectDossierApplication = Depends(get_dossier),
) -> dict[str, str | int]:
    version = dossier.create_version(actor, project_id, request.label)
    return {
        "product_version_id": str(version.product_version_id),
        "project_id": str(version.project_id),
        "version_number": version.version_number,
    }


@router.post("/product-versions/{version_id}/materials:initiate", status_code=201)
def initiate_material(
    version_id: UUID,
    request: InitiateMaterialRequest,
    actor: Actor = Depends(get_actor),
    dossier: ProjectDossierApplication = Depends(get_dossier),
) -> dict[str, str]:
    upload = dossier.initiate_material(actor, version_id, **request.model_dump())
    return {
        "material_id": str(upload.material_id),
        "object_key": upload.object_key,
        "upload_url": upload.upload_url,
    }


@router.post("/materials/{material_id}/complete")
def complete_material(
    material_id: UUID,
    actor: Actor = Depends(get_actor),
    dossier: ProjectDossierApplication = Depends(get_dossier),
) -> dict[str, str | None]:
    material = dossier.complete_material(actor, material_id)
    return {
        "material_id": str(material.material_id),
        "status": material.status.value,
        "reason": material.rejection_reason,
    }


@router.post("/product-versions/{version_id}/gap-questions")
def generate_gap_questions(
    version_id: UUID,
    actor: Actor = Depends(get_actor),
    request_correlation_id: UUID = Depends(correlation_id),
    dossier: ProjectDossierApplication = Depends(get_dossier),
) -> dict[str, object]:
    draft, questions = dossier.diagnose_gaps(actor, version_id, request_correlation_id)
    return {
        "correlation_id": str(request_correlation_id),
        "profile_draft": draft.response_view(),
        "questions": [
            {
                "question_id": str(item.question_id),
                "field": item.field,
                "question": item.question,
                "priority": item.priority,
            }
            for item in questions
        ],
    }


@router.post("/product-versions/{version_id}/gap-answers")
def answer_gap_questions(
    version_id: UUID,
    request: AnswerGapsRequest,
    actor: Actor = Depends(get_actor),
    dossier: ProjectDossierApplication = Depends(get_dossier),
) -> dict[str, object]:
    draft = dossier.answer_gaps(actor, version_id, request.correlation_id, request.answers)
    return {"correlation_id": str(request.correlation_id), "profile_draft": draft.response_view()}


@router.post("/product-versions/{version_id}/profile-confirmations", status_code=201)
def confirm_profile(
    version_id: UUID,
    request: ConfirmProfileRequest,
    actor: Actor = Depends(get_actor),
    dossier: ProjectDossierApplication = Depends(get_dossier),
) -> dict[str, object]:
    profile = dossier.confirm_profile(actor, version_id, request.acknowledge_model_inference)
    return {
        "profile_id": str(profile.profile_id),
        "product_version_id": str(profile.product_version_id),
        "confirmed_by": profile.confirmed_by,
        "confirmed_fields": profile.fields,
    }


@router.post("/product-versions/{version_id}/plan")
def enter_planned(
    version_id: UUID,
    actor: Actor = Depends(get_actor),
    request_correlation_id: UUID = Depends(correlation_id),
    dossier: ProjectDossierApplication = Depends(get_dossier),
) -> dict[str, str]:
    run = dossier.plan(actor, version_id, request_correlation_id)
    return {"run_id": str(run.run_id), "status": run.status.value, "correlation_id": str(request_correlation_id)}


__all__ = ["router"]
