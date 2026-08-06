# ruff: noqa: B008
"""Service-authenticated Matrix event ingress for the AgentTeams bridge."""

from __future__ import annotations

import hmac
import json
import os
from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request

from launchscope_api.infrastructure.db.session import DatabaseSettings, create_database_engine, session_factory
from launchscope_api.infrastructure.object_store import S3QuarantineObjectStore
from launchscope_api.modules.identity_tenant.application import Actor

from .handoff_application import HandoffApplication

router = APIRouter(prefix="/internal/agentteams", tags=["AgentTeams bridge"])


class _ConfiguredDirectory:
    def __init__(self, raw: str) -> None:
        value = json.loads(raw or "{}")
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()
        ):
            raise RuntimeError("LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON must be an MXID-to-Agent object")
        self._mapping = value

    def agent_for_mxid(self, mxid: str) -> str | None:
        return self._mapping.get(mxid)


@lru_cache(maxsize=1)
def _from_env() -> HandoffApplication:
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(
        settings.url, application_role=os.getenv("LAUNCHSCOPE_DB_ROLE", "launchscope_runtime")
    )
    return HandoffApplication(
        session_factory(engine), S3QuarantineObjectStore.from_env(),
        _ConfiguredDirectory(os.getenv("LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON", "{}")),
    )


def _authorize(value: str | None) -> None:
    expected = os.getenv("LAUNCHSCOPE_AGENTTEAMS_BRIDGE_TOKEN", "")
    supplied = value.removeprefix("Bearer ") if value else ""
    if not expected or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="valid AgentTeams bridge credential required")


@router.post("/matrix-events", status_code=202)
def matrix_event(
    body: dict[str, object],
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    tenant_id: UUID = Header(alias="X-LaunchScope-Tenant-Id"),
    run_id: UUID = Header(alias="X-LaunchScope-Run-Id"),
    task_id: UUID = Header(alias="X-LaunchScope-Task-Id"),
) -> dict[str, object]:
    _authorize(authorization)
    application = getattr(request.app.state, "handoff_application", None) or _from_env()
    result = application.consume(Actor(tenant_id, "agentteams-matrix-bridge"), body, run_id=run_id, task_id=task_id)
    return {
        "matrix_event_id": result.matrix_event_id, "task_status": result.task_status,
        "run_status": result.run_status, "duplicate": result.duplicate,
        "report_id": str(result.report_id) if result.report_id else None,
    }


__all__ = ["router"]
