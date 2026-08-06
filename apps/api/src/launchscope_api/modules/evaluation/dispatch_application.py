"""Atomic PLANNED Run dispatch into the durable 1+5 asynchronous graph."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    agent_identity,
    agentteams_run_binding,
    budget_reservation,
    evidence,
    evaluation_run,
    material,
    run_manifest,
    run_status_history,
    stage,
    task,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_domain.value_objects import TenantScope
from launchscope_orchestrator.manifest_loader import AgentManifestLoader
from launchscope_skills import SkillRegistry

from .task_dispatch import enqueue_ready_tasks, provider_usage_required


@dataclass(frozen=True, slots=True)
class DispatchResult:
    run_id: UUID
    status: str
    manifest_sha256: str
    task_count: int


_TASKS = (
    ("leader-plan", "LEADER_PLANNING", "evaluation-manager", "launchscope-evaluation-manager-handoff-v1", ()),
    ("product", "DOMAIN_REVIEW", "product-engineering", "browser-product-audit", ("leader-plan",)),
    ("user", "DOMAIN_REVIEW", "user-evidence", "browser-product-audit", ("leader-plan",)),
    ("business", "DOMAIN_REVIEW", "business-investment", "business-investment-assessment", ("leader-plan",)),
    ("geo", "DOMAIN_REVIEW", "geo-policy-trend", "launchscope-geo-policy-trend-handoff-v1", ("leader-plan",)),
    ("audit", "EVIDENCE_AUDIT", "evidence-auditor", "evidence-grounding-audit", ("product", "user", "business", "geo")),
    ("synthesis", "RULE_SYNTHESIS", "evaluation-manager", "launchscope-evaluation-manager-handoff-v1", ("audit",)),
)


class DispatchApplication:
    def __init__(self, sessions: sessionmaker[Session], *, budget_usd: Decimal = Decimal("20")) -> None:
        if budget_usd <= 0 or budget_usd > Decimal("20"):
            raise ValueError("Demo Run budget must be positive and no greater than USD 20")
        self._sessions = sessions
        self._budget_usd = budget_usd

    def dispatch(self, actor: Actor, run_id: UUID, *, idempotency_key: str) -> DispatchResult:
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            row = (
                session.execute(
                    select(evaluation_run)
                    .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFoundError("run was not found")
            existing_manifest = session.execute(
                select(run_manifest.c.manifest_sha256).where(
                    run_manifest.c.tenant_id == actor.tenant_id, run_manifest.c.run_id == run_id
                )
            ).scalar_one_or_none()
            if existing_manifest is not None:
                if row["status"] != "RUNNING":
                    raise ValueError("a dispatched Run cannot be replayed from its current status")
                count = session.execute(
                    select(task.c.id).where(task.c.tenant_id == actor.tenant_id, task.c.run_id == run_id)
                ).all()
                return DispatchResult(run_id, "RUNNING", existing_manifest, len(count))
            if row["status"] != "PLANNED":
                raise ValueError("dispatch requires a durable PLANNED Run")

            agents = AgentManifestLoader().load_all()
            skills = SkillRegistry().load_p0()
            manifest = {
                "schema_version": "2.0",
                "execution_mode": "AGENTTEAMS_V1_2_ROCKETMQ",
                "agentteams": {"version": "v1.2.0", "team": "launchscope-potential-review"},
                "agents": {item.code: {"version": item.version, "sha256": item.content_sha256} for item in agents},
                "skills": {
                    item.skill_code: {"version": item.version, "sha256": item.content_sha256}
                    for item in skills
                },
                "tools": {
                    "launchscope-context.get.v1": "1.0",
                    "browser-audit.v1": "1.0",
                    "public-research-search.v1": "1.0",
                },
                "model_policy": "environment-rendered-per-role-openai-compatible",
                "standard_version": row["standard_version"],
                "budget": {"currency": "USD", "hard_limit": str(self._budget_usd)},
                "limits": {
                    "model_calls": 12, "input_tokens": 200_000, "output_tokens": 50_000,
                    "search_queries": 8, "browser_seconds": 600, "task_timeout_seconds": 600,
                },
                "model_pricing": {
                    "input_usd_per_million_tokens": os.getenv("LAUNCHSCOPE_MODEL_INPUT_USD_PER_MILLION"),
                    "output_usd_per_million_tokens": os.getenv("LAUNCHSCOPE_MODEL_OUTPUT_USD_PER_MILLION"),
                    "required_before_submission": provider_usage_required(),
                },
                "failure_policy": {
                    "SUBMISSION_UNKNOWN": "NEEDS_ATTENTION_NO_RETRY",
                    "USAGE_UNKNOWN": "NEEDS_ATTENTION_NO_RETRY" if provider_usage_required() else "OPTIONAL_DEMO",
                    "TOOL_SIDE_EFFECT_UNKNOWN": "NEEDS_ATTENTION_NO_RETRY",
                },
            }
            manifest_sha = hashlib.sha256(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            session.execute(
                run_manifest.insert().values(
                    run_id=run_id,
                    tenant_id=actor.tenant_id,
                    frozen_config=manifest,
                    manifest_sha256=manifest_sha,
                    budget=manifest["budget"],
                    security_policy={"read_only_tools": True, "external_actions": "DENY"},
                    created_at=now,
                )
            )
            session.execute(
                budget_reservation.insert().values(
                    id=uuid4(), tenant_id=actor.tenant_id, run_id=run_id, category="run_total",
                    currency="USD", limit_amount=self._budget_usd, reserved_amount=self._budget_usd,
                    consumed_amount=Decimal("0"), released_amount=Decimal("0"), status="RESERVED",
                    idempotency_key=f"dispatch:{run_id}:budget", created_at=now, updated_at=now,
                )
            )
            session.execute(
                agentteams_run_binding.insert().values(
                    id=uuid4(), tenant_id=actor.tenant_id, run_id=run_id,
                    agentteams_version="v1.2.0", team_name="launchscope-potential-review",
                    binding_status="PENDING_MANAGER_ACK", created_at=now, updated_at=now,
                )
            )
            stage_ids = {
                code: uuid4() for code in ("LEADER_PLANNING", "DOMAIN_REVIEW", "EVIDENCE_AUDIT", "RULE_SYNTHESIS")
            }
            for ordinal, (code, stage_id) in enumerate(stage_ids.items(), 1):
                session.execute(
                    stage.insert().values(
                        id=stage_id, tenant_id=actor.tenant_id, run_id=run_id, code=code,
                        ordinal=ordinal, status="READY" if ordinal == 1 else "BLOCKED",
                    )
                )
            identity_ids: dict[str, UUID] = {
                str(code): identity_id
                for code, identity_id in session.execute(select(agent_identity.c.code, agent_identity.c.id)).all()
            }
            task_ids = {code: uuid4() for code, *_ in _TASKS}
            for code, stage_code, agent_code, skill_ref, dependencies in _TASKS:
                session.execute(
                    task.insert().values(
                        id=task_ids[code], tenant_id=actor.tenant_id, run_id=run_id,
                        stage_id=stage_ids[stage_code], agent_identity_id=identity_ids.get(agent_code),
                        skill_version_id=None, stage_code=stage_code, agent_identity_ref=f"{agent_code}@1.0",
                        skill_ref=skill_ref, skill_version="1.0",
                        status="READY" if not dependencies else "BLOCKED", lease_token=None,
                        idempotency_key=f"dispatch:{run_id}:task:{code}",
                        dependencies=[str(task_ids[item]) for item in dependencies],
                        tool_allowlist=self._tools_for(agent_code), budget_slice={"currency": "USD", "max": "4"},
                        timeout_seconds=600, success_condition={"schema": "AgentHandoffV1"},
                        evidence_requirement="Every non-hypothesis Claim must reference Evidence IDs",
                        required=True, correction_attempts=0, transient_retries=0, side_effect_started=False,
                        created_at=now, updated_at=now,
                    )
                )
            validated_materials = session.execute(select(material).where(
                material.c.tenant_id == actor.tenant_id,
                material.c.product_version_id == row["product_version_id"],
                material.c.ingest_status == "VALIDATED",
            )).mappings().all()
            for source in validated_materials:
                session.execute(evidence.insert().values(
                    id=uuid4(), tenant_id=actor.tenant_id, run_id=run_id, task_id=None,
                    material_id=source["id"], source_type="MATERIAL", object_key=source["object_key"],
                    sha256=source["sha256"], size_bytes=source["size_bytes"], mime_type=source["mime_type"],
                    evidence_level="E1", trust_level=source["trust_level"],
                    summary=f"Validated project material: {source['display_name']}"[:4000],
                    published_at=None, fetched_at=source["submitted_at"], valid_from=None, valid_until=None,
                    region=None, simulated=False, supersedes_id=None, created_at=now,
                ))
            session.execute(
                update(evaluation_run)
                .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                .values(status="RUNNING", current_stage="LEADER_PLANNING", updated_at=now)
            )
            session.execute(
                run_status_history.insert().values(
                    id=uuid4(), tenant_id=actor.tenant_id, run_id=run_id,
                    from_status="PLANNED", to_status="RUNNING", reason="AgentTeams dispatch committed", occurred_at=now,
                )
            )
            if enqueue_ready_tasks(session, actor.tenant_id, run_id, "LEADER_PLANNING") != 1:
                raise RuntimeError("dispatch must create exactly one ready Leader task")
            return DispatchResult(run_id, "RUNNING", manifest_sha, len(_TASKS))

    @staticmethod
    def _tools_for(agent_code: str) -> list[str]:
        tools = ["launchscope-context.get.v1"]
        if agent_code in {"product-engineering", "user-evidence"}:
            tools.append("browser-audit.v1")
        if agent_code in {"user-evidence", "business-investment", "geo-policy-trend"}:
            tools.append("public-research-search.v1")
        return tools


__all__ = ["DispatchApplication", "DispatchResult"]
