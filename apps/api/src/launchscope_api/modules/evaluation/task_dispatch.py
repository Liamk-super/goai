"""Create bounded task-ready events for AgentTeams Matrix delivery."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from launchscope_api.infrastructure.db.schema import evaluation_run, run_execution_control, run_manifest, task
from launchscope_api.infrastructure.messaging.outbox import OutboxRepository
from launchscope_api.modules.evidence.task_capability import issue_task_capability
from launchscope_domain.events import EventEnvelope
from launchscope_domain.value_objects import TenantScope
from launchscope_orchestrator.agentteams_bridge import AgentHandoffV1, AgentHandoffV2

_ROOT = Path(__file__).resolve().parents[6]
_V4_CONTRACT_BY_STAGE = {
    "LEADER_PLANNING": ("ManagerPlanV1", "manager/manager-plan.v1.json"),
    "DOMAIN_REVIEW": ("AgentHandoffV3", "handoffs/agent-handoff.v3.json"),
    "TARGETED_REMEDIATION": ("AgentHandoffV3", "handoffs/agent-handoff.v3.json"),
    "EVIDENCE_AUDIT": ("AuditResultV3", "audit/audit-result.v3.json"),
    "SUPERVISOR_SYNTHESIS": ("ManagerSynthesisV1", "manager/manager-synthesis.v1.json"),
}
_V5_CONTRACT_BY_STAGE = {
    **_V4_CONTRACT_BY_STAGE,
    "LEADER_PLANNING": ("ManagerPlanV2", "manager/manager-plan.v2.json"),
}
_V6_CONTRACT_BY_STAGE = {
    **_V5_CONTRACT_BY_STAGE,
    "EVIDENCE_AUDIT": ("AuditResultV4", "audit/audit-result.v4.json"),
    "SUPERVISOR_SYNTHESIS": ("ManagerSynthesisV2", "manager/manager-synthesis.v2.json"),
}


def _configured_agent_max_iters(limits: Mapping[str, object], agent_code: str) -> int:
    by_agent = limits.get("agent_iterations_by_agent", {})
    manifest_limit = int(by_agent.get(agent_code, 16)) if isinstance(by_agent, Mapping) else 16
    configured_limit = os.getenv("LAUNCHSCOPE_COPAW_MAX_ITERS")
    if agent_code == "user-evidence":
        configured_limit = os.getenv("LAUNCHSCOPE_USER_COPAW_MAX_ITERS") or configured_limit
    if configured_limit is None or not configured_limit.strip():
        return manifest_limit
    operational_limit = int(configured_limit)
    if not 1 <= operational_limit <= 256:
        raise ValueError("configured CoPaw max iterations must be between 1 and 256")
    return max(manifest_limit, operational_limit)


def provider_usage_required() -> bool:
    return os.getenv("LAUNCHSCOPE_REQUIRE_PROVIDER_USAGE", "true").strip().lower() in {"1", "true", "yes"}


def provider_cost_mode() -> str:
    mode = os.getenv("LAUNCHSCOPE_PROVIDER_COST_MODE", "TOKEN_ONLY").strip().upper()
    if mode not in {"EXACT", "TOKEN_ONLY"}:
        raise ValueError("LAUNCHSCOPE_PROVIDER_COST_MODE must be EXACT or TOKEN_ONLY")
    return mode


def _as_list(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _research_policy(
    tool_allowlist: list[object], limits: Mapping[str, object], authorized_urls: list[object]
) -> dict[str, object]:
    external_tool_codes = {"browser-audit.v1", "public-research-search.v1"}
    external_tools_enabled = any(str(item) in external_tool_codes for item in tool_allowlist)
    globally_material_only = os.getenv("LAUNCHSCOPE_MATERIAL_ONLY", "false").lower() == "true"
    return {
        "material_only": globally_material_only or not external_tools_enabled,
        "external_tools_required": not globally_material_only and external_tools_enabled,
        "authorized_urls": authorized_urls,
        "browser_calls_per_task": (
            int(str(limits.get("browser_calls_per_task", 0))) if "browser-audit.v1" in tool_allowlist else 0
        ),
        "search_queries_per_task": (
            int(str(limits.get("search_queries", 0))) if "public-research-search.v1" in tool_allowlist else 0
        ),
    }


def _assignment_contract(
    frozen_config: Mapping[str, object], stage_code: str, *, dispatch_epoch: int = 0
) -> tuple[str | None, dict[str, object]]:
    generation = frozen_config.get("agent_contract_generation")
    if generation in {"v4", "v5", "v6"}:
        if generation == "v6" and stage_code in {"DOMAIN_REVIEW", "TARGETED_REMEDIATION"} and dispatch_epoch > 1:
            schema = json.loads(
                (_ROOT / "packages/contracts/handoffs/agent-handoff.v4.json").read_text(encoding="utf-8")
            )
            return "AgentHandoffV4", schema
        contract_map = (
            _V6_CONTRACT_BY_STAGE
            if generation == "v6"
            else _V5_CONTRACT_BY_STAGE
            if generation == "v5"
            else _V4_CONTRACT_BY_STAGE
        )
        message_type, relative_path = contract_map[stage_code]
        schema = json.loads((_ROOT / "packages/contracts" / relative_path).read_text(encoding="utf-8"))
        return message_type, schema
    if generation == "v3":
        return None, AgentHandoffV2.model_json_schema()
    return None, AgentHandoffV1.model_json_schema()


def enqueue_ready_tasks(session: Session, tenant_id: UUID, run_id: UUID, stage_code: str) -> int:
    run = session.execute(select(
        evaluation_run.c.correlation_id,
    ).where(
        evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id,
    )).one()
    control = session.execute(
        select(run_execution_control.c.state, run_execution_control.c.control_epoch).where(
            run_execution_control.c.tenant_id == tenant_id,
            run_execution_control.c.run_id == run_id,
        )
    ).mappings().one_or_none()
    if control is None or control["state"] != "ACTIVE":
        return 0
    manifest = session.execute(select(
        run_manifest.c.manifest_sha256,
        run_manifest.c.frozen_config,
    ).where(
        run_manifest.c.tenant_id == tenant_id, run_manifest.c.run_id == run_id,
    )).mappings().one()
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
        # ADR 0004: a clarification re-dispatch is a genuinely new delivery of the
        # same Task, so the epoch keeps the Outbox key unique instead of colliding
        # with the original task-ready message.
        epoch = int(str(row.get("dispatch_epoch") or 0))
        limits = manifest["frozen_config"].get("limits", {})
        message_type, handoff_schema = _assignment_contract(
            manifest["frozen_config"], str(row["stage_code"]), dispatch_epoch=epoch
        )
        event = EventEnvelope(
            event_type="evaluation.task.ready.v1",
            tenant_id=tenant_id,
            run_id=run_id,
            task_id=task_id,
            payload={
                "manifest_sha256": manifest["manifest_sha256"],
                "team_name": "launchscope-potential-review",
                "agent_code": agent_code,
                "agent_contract_generation": str(manifest["frozen_config"].get("agent_contract_generation") or ""),
                "dispatch_epoch": epoch,
                "control_epoch": int(control["control_epoch"]),
                "stage_code": str(row["stage_code"]),
                "skill_ref": str(row["skill_ref"]),
                "dependencies": dependencies,
                "tool_allowlist": tool_allowlist,
                "evidence_requirement": str(row["evidence_requirement"]),
                "context_token": issue_task_capability(
                    tenant_id,
                    run_id,
                    task_id,
                    agent_code,
                    allowed_tools=tuple(str(item) for item in tool_allowlist),
                    control_epoch=int(control["control_epoch"]),
                ),
                "handoff_schema": handoff_schema,
                **({"message_type": message_type} if message_type else {}),
                "usage_policy": {
                    "required": provider_usage_required(),
                    "cost_mode": provider_cost_mode(),
                    "mode": "DEMO",
                },
                "research_policy": _research_policy(
                    tool_allowlist,
                    limits,
                    list(manifest["frozen_config"].get("research_targets", {}).get("authorized_urls", [])),
                ),
                "agent_runtime": {
                    "max_iters": _configured_agent_max_iters(limits, agent_code),
                    "task_timeout_seconds": int(
                        limits.get("task_timeout_by_agent", {}).get(agent_code, row["timeout_seconds"])
                    ),
                },
            },
            correlation_id=run.correlation_id,
            idempotency_key=f"task-ready:{run_id}:{task_id}:{epoch}",
        )
        OutboxRepository(session).enqueue(event, aggregate_id=run_id, aggregate_type="evaluation_run", scope=scope)
    return len(rows)


__all__ = ["enqueue_ready_tasks", "provider_cost_mode", "provider_usage_required"]
