# ruff: noqa: B008
"""REST resources for Product Validation Scripts and user evidence."""

from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, Field

from launchscope_api.infrastructure.db.session import DatabaseSettings, create_database_engine, session_factory
from launchscope_api.infrastructure.object_store import S3QuarantineObjectStore
from launchscope_api.modules.identity_tenant.application import Actor

from .application import UserValidationApplication
from .runner import NodeUserValidationRunner

router = APIRouter(tags=["User validation"])


class ValidationTaskRequest(BaseModel):
    task_key: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    expected_observable_outcome: str = Field(min_length=1, max_length=2000)
    max_steps: int | None = Field(default=None, ge=1, le=100)


class PutValidationScriptRequest(BaseModel):
    tasks: list[ValidationTaskRequest] = Field(min_length=1, max_length=5)


class RegisterUserEvidenceRequest(BaseModel):
    object_key: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: str
    claimed_tier: str = Field(pattern=r"^E[0-5]$")
    source_tier: str | None = None
    source: str = Field(min_length=1, max_length=1000)
    observed_at: datetime
    expires_at: datetime | None = None
    sample_size: int | None = Field(default=None, ge=1)
    segment: str | None = Field(default=None, max_length=500)
    aggregate_observation: str = Field(min_length=1, max_length=4000)
    applicability: dict[str, object] = Field(default_factory=dict)
    supporting_claim_refs: list[str] = Field(default_factory=list, max_length=100)
    contradicting_claim_refs: list[str] = Field(default_factory=list, max_length=100)


@lru_cache(maxsize=1)
def _from_env() -> UserValidationApplication:
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime")
    )
    return UserValidationApplication(
        session_factory(engine), S3QuarantineObjectStore.from_env(), NodeUserValidationRunner()
    )


def get_application(request: Request) -> UserValidationApplication:
    configured = getattr(request.app.state, "user_validation_application", None)
    return configured if configured is not None else _from_env()


def get_actor(
    x_tenant_id: UUID = Header(alias="X-Tenant-Id"),
    x_actor_id: str = Header(alias="X-Actor-Id", min_length=1, max_length=255),
) -> Actor:
    return Actor(x_tenant_id, x_actor_id)


def write_headers(
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    correlation_id: str = Header(alias="X-Correlation-Id", min_length=1, max_length=200),
) -> tuple[str, str]:
    return idempotency_key, correlation_id


@router.put("/product-versions/{product_version_id}/user-validation-script")
def put_validation_script(
    product_version_id: UUID,
    payload: PutValidationScriptRequest,
    headers: tuple[str, str] = Depends(write_headers),
    actor: Actor = Depends(get_actor),
    application: UserValidationApplication = Depends(get_application),
) -> dict[str, object]:
    return application.put_script(
        actor,
        product_version_id,
        [item.model_dump() for item in payload.tasks],
        idempotency_key=headers[0],
        correlation_id=headers[1],
    )


@router.post("/product-versions/{product_version_id}/user-evidence", status_code=201)
def register_user_evidence(
    product_version_id: UUID,
    payload: RegisterUserEvidenceRequest,
    headers: tuple[str, str] = Depends(write_headers),
    actor: Actor = Depends(get_actor),
    application: UserValidationApplication = Depends(get_application),
) -> dict[str, object]:
    return application.register_evidence(
        actor,
        product_version_id,
        payload.model_dump(),
        idempotency_key=headers[0],
        correlation_id=headers[1],
    )


@router.post("/runs/{baseline_run_id}/user-evidence-rechecks", status_code=201)
def create_user_evidence_recheck(
    baseline_run_id: UUID,
    headers: tuple[str, str] = Depends(write_headers),
    actor: Actor = Depends(get_actor),
    application: UserValidationApplication = Depends(get_application),
) -> dict[str, object]:
    try:
        correlation_id = UUID(headers[1])
    except ValueError as exc:
        raise ValueError("X-Correlation-Id must be a UUID") from exc
    return application.create_recheck(
        actor,
        baseline_run_id,
        idempotency_key=headers[0],
        correlation_id=correlation_id,
    )


@router.get("/runs/{run_id}/user-validation-result")
def get_user_validation_result(
    run_id: UUID,
    response: Response,
    actor: Actor = Depends(get_actor),
    application: UserValidationApplication = Depends(get_application),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return application.get_result(actor, run_id)


@router.get("/runs/{run_id}/user-validation-reports/{variant}")
def get_user_validation_report(
    run_id: UUID,
    variant: Literal["summary", "full"],
    response: Response,
    report_format: Literal["html", "markdown"] = Query(alias="format"),
    actor: Actor = Depends(get_actor),
    application: UserValidationApplication = Depends(get_application),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return application.get_report(actor, run_id, variant=variant, report_format=report_format)


__all__ = ["router"]
