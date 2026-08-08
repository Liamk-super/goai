"""Create bounded task-ready events for AgentTeams Matrix delivery."""

from __future__ import annotations

import os
from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from launchscope_api.infrastructure.db.schema import evaluation_run, run_manifest, task
from launchscope_api.infrastructure.messaging.outbox import OutboxRepository
from launchscope_api.modules.evidence.task_capability import issue_task_capability
from launchscope_domain.events import EventEnvelope
from launchscope_domain.value_objects import TenantScope
from launchscope_orchestrator.agentteams_bridge import AgentHandoffV1


def provider_usage_required() -> bool:
    return os.getenv("LAUNCHSCOPE_REQUIRE_PROVIDER_USAGE", "true").strip().lower() in {"1", "true", "yes"}


def _as_list(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def enqueue_ready_tasks(session: Session, tenant_id: UUID, run_id: UUID, stage_code: str) -> int:
    run = session.execute(select(
        evaluation_run.c.correlation_id,
    ).where(
        evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id,
    )).one()
    manifest_sha = session.execute(select(run_manifest.c.manifest_sha256).where(
        run_manifest.c.tenant_id == tenant_id, run_manifest.c.run_id == run_id,
    )).scalar_one()
    rows: list[Mapping[str, object]] = [
        dict(row)
        for row in session.execute(
            select(task)
            .where(
                task.c.tenant_id == tenant_id,
                task.c.run_id == run_id,
                task.c.stage_code == stage_code,
                task.c.status == "READY",
            )
            .order_by(task.c.created_at, task.c.id)
        ).mappings()
    ]
    scope = TenantScope(tenant_id)
    for row in rows:
        agent_code = str(row["agent_identity_ref"]).split("@", 1)[0]
        task_id = UUID(str(row["id"]))
        dependencies = _as_list(row["dependencies"])
        tool_allowlist = _as_list(row["tool_allowlist"])
        event = EventEnvelope(
            event_type="evaluation.task.ready.v1",
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=task_id,
            payload={
                "manifest_sha256": manifest_sha,
                "team_name": "launchscope-potential-review",
                "agent_code": agent_code,
                "stage_code": str(row["stage_code"]),
                "skill_ref": str(row["skill_ref"]),
                "dependencies": dependencies,
                "tool_allowlist": tool_allowlist,
                "evidence_requirement": str(row["evidence_requirement"]),
                "context_token": issue_task_capability(tenant_id, run_id, task_id, agent_code),
                "handoff_schema": AgentHandoffV1.model_json_schema(),
                "usage_policy": {"required": provider_usage_required(), "mode": "DEMO"},
                "research_policy": {
                    "material_only": os.getenv("LAUNCHSCOPE_MATERIAL_ONLY", "false").lower() == "true",
                    "external_tools_required": os.getenv("LAUNCHSCOPE_MATERIAL_ONLY", "false").lower() != "true",
                },
            },
            correlation_id=run.correlation_id,
            idempotency_key=f"task-ready:{run_id}:{task_id}",
        )
        OutboxRepository(session).enqueue(event, aggregate_id=run_id, aggregate_type="evaluation_run", scope=scope)
    return len(rows)


__all__ = ["enqueue_ready_tasks", "provider_usage_required"]
