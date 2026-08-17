# ruff: noqa: B008
"""T5 REST endpoints for project intake and confirmation."""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from pydantic import BaseModel, Field

from launchscope_api.modules.identity_tenant.application import Actor

from .application import ProjectDossierApplication
from .material_analysis import material_routing_enabled
from .model_extraction import IntakeModelExtractor
from .persistent_application import PersistentProjectDossierApplication

router = APIRouter(tags=["Projects", "Product versions", "Materials", "Intake"])
logger = logging.getLogger(__name__)


def _report_locale(value: str | None) -> str:
    return "en" if value and value.lower().startswith("en") else "zh-CN"


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


class PlanRequest(BaseModel):
    evaluation_mode: Literal["FULL_POTENTIAL", "USER_VALIDATION"] | None = None


class ExtractIntakeRequest(BaseModel):
    raw_content: str = Field(min_length=1, max_length=30_000)
    allow_external_processing: bool
    product_version_id: UUID | None = None


class AnalyzeVisualPageRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    page_number: int = Field(ge=1, le=10_000)
    image_data_url: str = Field(min_length=100, max_length=4_000_000)
    text_hint: str = Field(default="", max_length=4_000)
    local_table_detected: bool = False
    allow_external_processing: bool


class GenerateValidationTasksRequest(BaseModel):
    context: str = Field(min_length=1, max_length=30_000)
    allow_external_processing: bool


class RetryMaterialAnalysisRequest(BaseModel):
    allow_external_processing: bool


class MaterialSelectionItemRequest(BaseModel):
    material_id: UUID
    analysis_id: UUID
    decision: str
    acknowledged_uncovered_locators: list[dict[str, object]] = Field(default_factory=list)


class MaterialSelectionRequest(BaseModel):
    items: list[MaterialSelectionItemRequest] = Field(min_length=1, max_length=100)


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


def idempotency_key(value: str = Header(alias="Idempotency-Key", min_length=1, max_length=200)) -> str:
    return value


def optional_idempotency_key(value: str | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    return value


@router.post("/intake:extract")
def extract_intake(
    request: ExtractIntakeRequest,
    actor: Actor = Depends(get_actor),
    dossier: ProjectDossierApplication | PersistentProjectDossierApplication = Depends(get_dossier),
) -> dict[str, object]:
    """Return a model-inferred draft; this endpoint deliberately persists no fact."""
    try:
        material_context = ""
        if material_routing_enabled() and request.product_version_id and hasattr(dossier, "material_analysis"):
            material_context = dossier.material_analysis.included_context(actor, request.product_version_id)
        raw_content = "\n\n".join(value for value in (request.raw_content, material_context) if value)[:30_000]
        draft = IntakeModelExtractor().extract(
            raw_content,
            allow_external_processing=request.allow_external_processing,
        )
    except Exception:
        logger.warning(
            "intake model extraction failed",
            extra={"tenant_id": str(actor.tenant_id), "actor_id": actor.actor_id},
        )
        raise
    logger.info(
        "intake model extraction completed",
        extra={
            "tenant_id": str(actor.tenant_id),
            "actor_id": actor.actor_id,
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


@router.post("/intake:analyze-visual-page")
def analyze_visual_page(request: AnalyzeVisualPageRequest, _actor: Actor = Depends(get_actor)) -> dict[str, object]:
    return IntakeModelExtractor().analyze_visual_page(**request.model_dump())


@router.post("/intake:generate-validation-tasks")
def generate_validation_tasks(
    request: GenerateValidationTasksRequest,
    _actor: Actor = Depends(get_actor),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> dict[str, object]:
    return IntakeModelExtractor().generate_validation_tasks(
        request.context,
        allow_external_processing=request.allow_external_processing,
        locale=_report_locale(accept_language),
    )


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


@router.get("/product-versions/{version_id}/public-demo-disclosure")
def get_public_demo_disclosure(
    version_id: UUID,
    actor: Actor = Depends(get_actor),
    dossier: ProjectDossierApplication | PersistentProjectDossierApplication = Depends(get_dossier),
) -> dict[str, object]:
    if not hasattr(dossier, "public_demo_disclosure"):
        raise ValueError("public Demo disclosure requires PostgreSQL runtime")
    return dossier.public_demo_disclosure(actor, version_id)


@router.post("/product-versions/{version_id}/public-demo-disclosure:accept", status_code=201)
def accept_public_demo_disclosure(
    version_id: UUID,
    actor: Actor = Depends(get_actor),
    request_correlation_id: UUID = Depends(correlation_id),
    request_idempotency_key: str = Depends(idempotency_key),
    dossier: ProjectDossierApplication | PersistentProjectDossierApplication = Depends(get_dossier),
) -> dict[str, object]:
    if not hasattr(dossier, "accept_public_demo_disclosure"):
        raise ValueError("public Demo disclosure requires PostgreSQL runtime")
    return dossier.accept_public_demo_disclosure(
        actor,
        version_id,
        idempotency_key=request_idempotency_key,
        correlation_id=request_correlation_id,
    )


@router.post("/materials/{material_id}/complete")
def complete_material(
    material_id: UUID,
    background_tasks: BackgroundTasks,
    allow_external_processing: bool = False,
    actor: Actor = Depends(get_actor),
    request_correlation_id: UUID = Depends(correlation_id),
    request_idempotency_key: str | None = Depends(optional_idempotency_key),
    dossier: ProjectDossierApplication = Depends(get_dossier),
) -> dict[str, str | None]:
    material = dossier.complete_material(actor, material_id)
    response: dict[str, str | None] = {
        "material_id": str(material.material_id),
        "status": material.status.value,
        "reason": material.rejection_reason,
        "object_key": material.object_key,
        "sha256": material.expected_sha256,
    }
    if material_routing_enabled() and material.status.value == "VALIDATED" and hasattr(dossier, "material_analysis"):
        analysis = dossier.material_analysis.enqueue(
            actor,
            material_id,
            allow_external_processing=allow_external_processing,
            correlation_id=request_correlation_id,
            idempotency_key=(
                f"{request_idempotency_key}:analysis"
                if request_idempotency_key
                else f"legacy-material-complete:{material_id}:{material.expected_sha256}:analysis"
            ),
        )
        response["analysis_id"] = str(analysis["analysis_id"])
        response["analysis_status"] = str(analysis["status"])
        background_tasks.add_task(dossier.material_analysis.process, actor, UUID(str(analysis["analysis_id"])))
    return response


@router.get("/product-versions/{version_id}/material-analyses")
def list_material_analyses(
    version_id: UUID,
    actor: Actor = Depends(get_actor),
    dossier: ProjectDossierApplication = Depends(get_dossier),
) -> dict[str, object]:
    if not hasattr(dossier, "material_analysis"):
        return {"product_version_id": str(version_id), "items": []}
    return {
        "product_version_id": str(version_id),
        "items": dossier.material_analysis.list_for_version(actor, version_id),
    }


@router.post("/materials/{material_id}/analysis:retry", status_code=202)
def retry_material_analysis(
    material_id: UUID,
    request: RetryMaterialAnalysisRequest,
    background_tasks: BackgroundTasks,
    actor: Actor = Depends(get_actor),
    request_correlation_id: UUID = Depends(correlation_id),
    request_idempotency_key: str = Depends(idempotency_key),
    dossier: ProjectDossierApplication = Depends(get_dossier),
) -> dict[str, object]:
    if not hasattr(dossier, "material_analysis"):
        raise ValueError("durable material analysis requires PostgreSQL runtime")
    analysis = dossier.material_analysis.enqueue(
        actor,
        material_id,
        allow_external_processing=request.allow_external_processing,
        correlation_id=request_correlation_id,
        idempotency_key=request_idempotency_key,
        force_retry=True,
    )
    background_tasks.add_task(dossier.material_analysis.process, actor, UUID(str(analysis["analysis_id"])))
    return analysis


@router.post("/product-versions/{version_id}/material-selection", status_code=201)
def submit_material_selection(
    version_id: UUID,
    request: MaterialSelectionRequest,
    actor: Actor = Depends(get_actor),
    _request_correlation_id: UUID = Depends(correlation_id),
    request_idempotency_key: str = Depends(idempotency_key),
    dossier: ProjectDossierApplication = Depends(get_dossier),
) -> dict[str, object]:
    if not hasattr(dossier, "material_analysis"):
        raise ValueError("durable material selection requires PostgreSQL runtime")
    return dossier.material_analysis.submit_selection(
        actor,
        version_id,
        [item.model_dump() for item in request.items],
        idempotency_key=request_idempotency_key,
    )


@router.get("/product-versions/{version_id}/material-selection")
def get_material_selection(
    version_id: UUID,
    actor: Actor = Depends(get_actor),
    dossier: ProjectDossierApplication = Depends(get_dossier),
) -> dict[str, object]:
    if not hasattr(dossier, "material_analysis"):
        return {"selection": None}
    return {"selection": dossier.material_analysis.latest_selection(actor, version_id)}


@router.get("/product-versions/{version_id}/material-context")
def get_material_context(
    version_id: UUID,
    actor: Actor = Depends(get_actor),
    dossier: ProjectDossierApplication = Depends(get_dossier),
) -> dict[str, object]:
    if not hasattr(dossier, "material_analysis"):
        return {"context": ""}
    return {"context": dossier.material_analysis.included_context(actor, version_id)}


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
    request: PlanRequest | None = None,
    actor: Actor = Depends(get_actor),
    request_correlation_id: UUID = Depends(correlation_id),
    dossier: ProjectDossierApplication = Depends(get_dossier),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> dict[str, str]:
    run = dossier.plan(
        actor,
        version_id,
        request_correlation_id,
        locale=_report_locale(accept_language),
        evaluation_mode=request.evaluation_mode if request is not None else None,
    )
    return {"run_id": str(run.run_id), "status": run.status.value, "correlation_id": str(request_correlation_id)}


__all__ = ["router"]
