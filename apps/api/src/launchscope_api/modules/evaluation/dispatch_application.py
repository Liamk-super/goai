"""Admission for generation-v4 planning, with legacy dispatch retained read-only."""

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
    evaluation_run,
    evidence,
    material,
    run_manifest,
    run_status_history,
    skill_execution,
    skill_result,
    skill_version,
    stage,
    task,
    user_validation_script,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.infrastructure.object_store import S3QuarantineObjectStore
from launchscope_api.modules.evaluation.intake_application import IntakeValidationError
from launchscope_api.modules.evidence.mcp_application import configured_authorized_case_urls
from launchscope_api.modules.evidence.source_locator import (
    SourceLocatorRepository,
    internal_material_source_locator,
)
from launchscope_api.modules.evidence.tool_limits import BROWSER_CALLS_PER_TASK, SEARCH_QUERIES_PER_TASK
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_domain.value_objects import TenantScope
from launchscope_orchestrator.manifest_loader import AgentManifestLoader
from launchscope_skills import SkillRegistry

from .model_capability import configured_model_ids, model_usage_ledger_mode
from .runtime_mode import execution_runtime_unavailable_reason
from .task_dispatch import enqueue_ready_tasks, provider_cost_mode, provider_usage_required


@dataclass(frozen=True, slots=True)
class DispatchResult:
    run_id: UUID
    status: str
    manifest_sha256: str
    task_count: int


_RUNTIME_SKILL_HASHES = {
    "launchscope-evaluation-manager-handoff-v1": "461ef871f0d699f941c38fda21fe3f0c01cab376337341f0d10af0350d418201",
    "launchscope-geo-policy-trend-handoff-v1": "a382ac5f5ea6ce56dea4ac866a6299ae56d38fe9723d6b594549d6afe53e64ce",
}
_UVD_V2_RUNNER_SHA256 = "c062cccbd2a4a1258b5cd327a47e84064c6926f118c2ef7a11151b03922e8b84"
_UVD_V2_PROMPT_SHA256 = "95b5650e1b4b14da2f90918d623fee51e53a9580cfc59340ed169faeadbc12d9"
_UVD_V3_MANIFEST_SHA256 = "0964927ad124e301386b21626ef59f2f161230c55acc534b980b2a267d3ad285"
_UVD_V3_PACKAGE_SHA256 = "ac17e6b8c9b82760593a8a51eb2ab9959b0ff072a125f8237a19d52f21409192"
_UVD_V3_RUNNER_SHA256 = "f0923fd01aa203217b85d1c6683dc7783cf1af10019f13302dadda13d37b10f0"
_UVD_V3_PROMPT_SHA256 = "a46381cbe819f6e09ae7df196295989bd4b3261470474be201497debd2e341a2"
_UVD_KNOWLEDGE_SHA256 = "d5951922224c9d16e9b013139795d074c706c3f589f8ffec918c499e910300d2"
_UVD_INPUT_SCHEMA_SHA256 = "676b1d0968b1337bae5aa60dd148a17b94f885b874eddbb68cc1ea3ab816ce05"
_UVD_OUTPUT_SCHEMA_SHA256 = "81bfb80b385bfc9e3cb9429c88aae6eeeb40251c64817eccceed291c16990fbf"
TaskSpec = tuple[str, str, str, str, tuple[str, ...]]
_TASKS: tuple[TaskSpec, ...] = (
    ("leader-plan", "LEADER_PLANNING", "evaluation-manager", "launchscope-evaluation-manager-handoff-v1", ()),
    ("product", "DOMAIN_REVIEW", "product-engineering", "browser-product-audit", ("leader-plan",)),
    ("user", "DOMAIN_REVIEW", "user-evidence", "browser-product-audit", ("leader-plan",)),
    ("business", "DOMAIN_REVIEW", "business-investment", "business-investment-assessment", ("leader-plan",)),
    ("geo", "DOMAIN_REVIEW", "geo-policy-trend", "launchscope-geo-policy-trend-handoff-v1", ("leader-plan",)),
    ("audit", "EVIDENCE_AUDIT", "evidence-auditor", "evidence-grounding-audit", ("product", "user", "business", "geo")),
    ("synthesis", "RULE_SYNTHESIS", "evaluation-manager", "launchscope-evaluation-manager-handoff-v1", ("audit",)),
)
_RECHECK_TASKS: tuple[TaskSpec, ...] = (
    ("user", "DOMAIN_REVIEW", "user-evidence", "user-validation-designer", ()),
    ("audit", "EVIDENCE_AUDIT", "evidence-auditor", "evidence-grounding-audit", ("user",)),
    ("synthesis", "RULE_SYNTHESIS", "evaluation-manager", "launchscope-evaluation-manager-handoff-v1", ("audit",)),
)


class DispatchApplication:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        objects: S3QuarantineObjectStore | None = None,
        *,
        budget_usd: Decimal = Decimal("20"),
    ) -> None:
        if budget_usd <= 0 or budget_usd > Decimal("20"):
            raise ValueError("Demo Run budget must be positive and no greater than USD 20")
        self._sessions = sessions
        self._objects = objects
        self._budget_usd = budget_usd

    def dispatch(self, actor: Actor, run_id: UUID, *, idempotency_key: str) -> DispatchResult:
        from launchscope_api.modules.supervisor.intake_application import (
            ConfirmedProfileBriefBuilder,
            supervisor_1p4_enabled,
        )

        if not supervisor_1p4_enabled():
            raise IntakeValidationError(
                "SUPERVISOR_1P4_DISABLED: new evaluation dispatch is closed; legacy 1+5 fallback is prohibited"
            )
        runtime_unavailable = execution_runtime_unavailable_reason()
        if runtime_unavailable is not None:
            raise IntakeValidationError(f"EXECUTION_RUNTIME_UNAVAILABLE: {runtime_unavailable}")
        ConfirmedProfileBriefBuilder(self._sessions, self._objects).ensure_ready(actor, run_id)
        from launchscope_api.modules.supervisor.planning_application import ManagerPlanningApplication

        started = ManagerPlanningApplication(self._sessions).start_planning(actor, run_id)
        return DispatchResult(run_id, "RUNNING", started.manifest_sha256, 1)

    def _dispatch_legacy_for_historical_tests_only(
        self, actor: Actor, run_id: UUID, *, idempotency_key: str
    ) -> DispatchResult:
        """Keep the frozen legacy implementation callable by generation-pinned tests, never by REST admission."""
        now = datetime.now(UTC)
        user_validation_enabled = os.getenv("LAUNCHSCOPE_USER_VALIDATION_ENABLED", "false").lower() == "true"
        agent_generation = "v3" if user_validation_enabled else "v2"
        agent_identity_version = "3.0" if user_validation_enabled else "2.0"
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

            # ADR 0004: the running team uses the v2 identity generation, which is the
            # one that authorizes `information_request` as an Agent output.  The
            # generation is frozen into the manifest so a later verification resolves
            # the exact contract this Run executed under.
            agents = AgentManifestLoader().load_all(agent_generation)
            if user_validation_enabled:
                undrained = session.execute(
                    select(skill_execution.c.id).where(
                        skill_execution.c.tenant_id == actor.tenant_id,
                        skill_execution.c.skill_code == "user-validation-designer",
                        skill_execution.c.skill_version == "1.0.4",
                        skill_execution.c.status.in_(("AWAITING_STEP", "NEEDS_ATTENTION")),
                    ).limit(1)
                ).scalar_one_or_none()
                if undrained is not None:
                    raise RuntimeError("user-validation-designer 1.0.4 executions must drain before 1.0.5 cutover")
            skills = SkillRegistry().load_p0_v3() if user_validation_enabled else SkillRegistry().load_p0()
            authorized_urls = configured_authorized_case_urls()
            expected_skills = {
                **{item.skill_code: (item.version, item.content_sha256) for item in skills},
                **{code: ("1.0", digest) for code, digest in _RUNTIME_SKILL_HASHES.items()},
            }
            skill_rows = session.execute(select(
                skill_version.c.id,
                skill_version.c.skill_code,
                skill_version.c.version,
                skill_version.c.manifest_sha256,
            ).where(
                skill_version.c.skill_code.in_(expected_skills),
                skill_version.c.version.in_({version for version, _digest in expected_skills.values()}),
            )).mappings().all()
            skill_ids = {
                str(item["skill_code"]): item["id"]
                for item in skill_rows
                if expected_skills.get(str(item["skill_code"]), (None, None))[0] == str(item["version"])
            }
            actual_skill_hashes = {
                str(item["skill_code"]): str(item["manifest_sha256"])
                for item in skill_rows
                if expected_skills.get(str(item["skill_code"]), (None, None))[0] == str(item["version"])
            }
            if actual_skill_hashes != {code: digest for code, (_version, digest) in expected_skills.items()}:
                raise RuntimeError("database Skill versions do not match the frozen runtime catalog")
            run_tasks = _RECHECK_TASKS if row["run_kind"] == "USER_EVIDENCE_RECHECK" else _TASKS
            if user_validation_enabled and row["run_kind"] != "USER_EVIDENCE_RECHECK":
                run_tasks = tuple(
                    (
                        code,
                        stage_code,
                        agent_code,
                        "user-validation-designer" if code == "user" else skill_ref,
                        dependencies,
                    )
                    for code, stage_code, agent_code, skill_ref, dependencies in run_tasks
                )
            validation_script = session.execute(select(user_validation_script).where(
                user_validation_script.c.tenant_id == actor.tenant_id,
                user_validation_script.c.product_version_id == row["product_version_id"],
            ).order_by(user_validation_script.c.revision.desc())).mappings().first()
            validation_mode = "disabled"
            validation_change_reason: str | None = None
            if user_validation_enabled:
                if validation_script is None:
                    raise ValueError("enabled user validation requires a frozen Product Validation Script")
                validation_mode = (
                    "evidence_recheck" if row["run_kind"] == "USER_EVIDENCE_RECHECK" else "first_validation"
                )
                if validation_mode == "evidence_recheck":
                    validation_change_reason = "explicit_user_evidence_recheck"
                if validation_mode == "first_validation":
                    previous_results = session.execute(select(
                        skill_result.c.summary,
                        evaluation_run.c.standard_version,
                    ).join(
                        evaluation_run,
                        (evaluation_run.c.tenant_id == skill_result.c.tenant_id)
                        & (evaluation_run.c.id == skill_result.c.run_id),
                    ).where(
                        skill_result.c.tenant_id == actor.tenant_id,
                        evaluation_run.c.project_id == row["project_id"],
                        evaluation_run.c.id != run_id,
                        skill_result.c.status.in_(("COMPLETED", "PARTIAL")),
                    )).mappings().all()
                    if any(
                        item["standard_version"] == row["standard_version"]
                        and isinstance(item["summary"], dict)
                        and item["summary"].get("product_tasks_hash") == validation_script["product_tasks_sha256"]
                        for item in previous_results
                    ):
                        validation_mode = "version_regression"
                    elif not previous_results:
                        validation_change_reason = "no_completed_user_validation_history"
                    else:
                        same_standard = any(
                            item["standard_version"] == row["standard_version"] for item in previous_results
                        )
                        validation_change_reason = (
                            "validation_script_hash_changed" if same_standard else "standard_version_changed"
                        )
            user_model_id = os.getenv("AGENTTEAMS_MODEL_ID", "").strip() or "UNCONFIGURED"
            user_model_descriptor = {
                "api_style": "openai-compatible",
                "model_id": user_model_id,
            }
            user_model_sha256 = hashlib.sha256(
                json.dumps(user_model_descriptor, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            manifest = {
                "schema_version": "3.0" if user_validation_enabled else "2.0",
                "execution_mode": "AGENTTEAMS_V1_2_ROCKETMQ",
                "agentteams": {"version": "v1.2.0", "team": "launchscope-potential-review"},
                "agent_contract_generation": agent_generation,
                "agents": {item.code: {"version": item.version, "sha256": item.content_sha256} for item in agents},
                "skills": {
                    code: {"version": version, "sha256": digest}
                    for code, (version, digest) in sorted(expected_skills.items())
                },
                "tools": {
                    "launchscope-context.get.v1": "1.0",
                    "browser-audit.v1": "1.0",
                    "public-research-search.v1": "1.0",
                    **({
                        "user-validation-designer.start.v1": "1.0",
                        "user-validation-designer.submit-step.v1": "1.0",
                        "user-validation-designer.resume.v1": "1.0",
                        "user-validation-audit-context.get.v1": "1.0",
                    } if user_validation_enabled else {}),
                },
                "model_policy": "environment-rendered-per-role-openai-compatible",
                "model_runtime": {
                    "allowed_model_ids": configured_model_ids(),
                    "max_output_tokens_per_call": int(
                        os.getenv("LAUNCHSCOPE_MODEL_MAX_OUTPUT_TOKENS", "32768")
                    ),
                    "user-evidence": {
                        **user_model_descriptor,
                        "configuration_sha256": user_model_sha256,
                    }
                },
                "model_accounting": {"mode": model_usage_ledger_mode()},
                "research_targets": {"authorized_urls": list(authorized_urls)},
                "standard_version": row["standard_version"],
                "budget": {"currency": "USD", "hard_limit": str(self._budget_usd)},
                "limits": {
                    "model_calls": 256, "input_tokens": 5_000_000, "output_tokens": 500_000,
                    "search_queries": SEARCH_QUERIES_PER_TASK,
                    "browser_calls_per_task": BROWSER_CALLS_PER_TASK,
                    "browser_seconds": 600,
                    "task_timeout_seconds": 600,
                    "agent_iterations_by_agent": {"user-evidence": 32} if user_validation_enabled else {},
                    "task_timeout_by_agent": {"user-evidence": 1200} if user_validation_enabled else {},
                },
                "model_pricing": {
                    "cost_mode": provider_cost_mode(),
                    "input_usd_per_million_tokens": os.getenv("LAUNCHSCOPE_MODEL_INPUT_USD_PER_MILLION"),
                    "output_usd_per_million_tokens": os.getenv("LAUNCHSCOPE_MODEL_OUTPUT_USD_PER_MILLION"),
                    "required_before_submission": provider_usage_required(),
                },
                "failure_policy": {
                    "SUBMISSION_UNKNOWN": "NEEDS_ATTENTION_NO_RETRY",
                    "USAGE_UNKNOWN": "NEEDS_ATTENTION_NO_RETRY" if provider_usage_required() else "OPTIONAL_DEMO",
                    "TOOL_SIDE_EFFECT_UNKNOWN": "NEEDS_ATTENTION_NO_RETRY",
                },
                "user_validation": {
                    "enabled": user_validation_enabled,
                    "mode": validation_mode,
                    "change_reason": validation_change_reason,
                    "validation_script_sha256": validation_script["sha256"] if validation_script is not None else None,
                    "skill_version": "1.0.5" if user_validation_enabled else "1.0.4",
                    "runner_sha256": _UVD_V3_RUNNER_SHA256 if user_validation_enabled else _UVD_V2_RUNNER_SHA256,
                    "prompt_sha256": _UVD_V3_PROMPT_SHA256 if user_validation_enabled else _UVD_V2_PROMPT_SHA256,
                    "knowledge_package_sha256": _UVD_KNOWLEDGE_SHA256,
                    **({
                        "presentation_version": "0.4",
                        "skill_manifest_sha256": _UVD_V3_MANIFEST_SHA256,
                        "package_sha256": _UVD_V3_PACKAGE_SHA256,
                        "input_schema_sha256": _UVD_INPUT_SCHEMA_SHA256,
                        "output_schema_sha256": _UVD_OUTPUT_SCHEMA_SHA256,
                    } if user_validation_enabled else {}),
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
            all_stage_codes = ("LEADER_PLANNING", "DOMAIN_REVIEW", "EVIDENCE_AUDIT", "RULE_SYNTHESIS")
            active_stage_codes = {item[1] for item in run_tasks}
            stage_ids = {code: uuid4() for code in all_stage_codes if code in active_stage_codes}
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
            task_ids = {code: uuid4() for code, *_ in run_tasks}
            for code, stage_code, agent_code, skill_ref, dependencies in run_tasks:
                skill_version_value = expected_skills[skill_ref][0]
                timeout_seconds = 1200 if user_validation_enabled and agent_code == "user-evidence" else 600
                session.execute(
                    task.insert().values(
                        id=task_ids[code], tenant_id=actor.tenant_id, run_id=run_id,
                        stage_id=stage_ids[stage_code], agent_identity_id=identity_ids.get(agent_code),
                        skill_version_id=skill_ids[skill_ref], stage_code=stage_code,
                        agent_identity_ref=f"{agent_code}@{agent_identity_version}",
                        skill_ref=skill_ref, skill_version=skill_version_value,
                        status="READY" if not dependencies else "BLOCKED", lease_token=None,
                        idempotency_key=f"dispatch:{run_id}:task:{code}",
                        dependencies=[str(task_ids[item]) for item in dependencies],
                        tool_allowlist=self._tools_for(agent_code), budget_slice={"currency": "USD", "max": "4"},
                        timeout_seconds=timeout_seconds,
                        success_condition={"schema": "AgentHandoffV2" if user_validation_enabled else "AgentHandoffV1"},
                        evidence_requirement="Every non-hypothesis Claim must reference Evidence IDs",
                        required=True, correction_attempts=0, transient_retries=0, dispatch_epoch=0,
                        side_effect_started=False,
                        created_at=now, updated_at=now,
                    )
                )
            validated_materials = session.execute(select(material).where(
                material.c.tenant_id == actor.tenant_id,
                material.c.product_version_id == row["product_version_id"],
                material.c.ingest_status == "VALIDATED",
            )).mappings().all()
            for source in validated_materials:
                evidence_id = uuid4()
                session.execute(evidence.insert().values(
                    id=evidence_id, tenant_id=actor.tenant_id, run_id=run_id, task_id=None,
                    material_id=source["id"], source_type="MATERIAL", object_key=source["object_key"],
                    sha256=source["sha256"], size_bytes=source["size_bytes"], mime_type=source["mime_type"],
                    evidence_level="E1", trust_level=source["trust_level"],
                    summary=f"Validated project material: {source['display_name']}"[:4000],
                    published_at=None, fetched_at=source["submitted_at"], valid_from=None, valid_until=None,
                    region=None, simulated=False, supersedes_id=None, created_at=now,
                ))
                SourceLocatorRepository().append(
                    session,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    evidence_id=evidence_id,
                    locators=(
                        internal_material_source_locator(
                            display_name=source["display_name"],
                            fetched_at=source["submitted_at"] or source["created_at"] or now,
                            content_sha256=source["sha256"],
                            locator={"display_name": source["display_name"]},
                        ),
                    ),
                )
            session.execute(
                update(evaluation_run)
                .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                .values(status="RUNNING", current_stage=next(iter(stage_ids)), updated_at=now)
            )
            session.execute(
                run_status_history.insert().values(
                    id=uuid4(), tenant_id=actor.tenant_id, run_id=run_id,
                    from_status="PLANNED", to_status="RUNNING", reason="AgentTeams dispatch committed", occurred_at=now,
                )
            )
            first_stage = next(iter(stage_ids))
            if enqueue_ready_tasks(session, actor.tenant_id, run_id, first_stage) != 1:
                raise RuntimeError("dispatch must create exactly one initial ready task")
            return DispatchResult(run_id, "RUNNING", manifest_sha, len(run_tasks))

    @staticmethod
    def _tools_for(agent_code: str) -> list[str]:
        tools = ["launchscope-context.get.v1"]
        if agent_code in {"product-engineering", "user-evidence"}:
            tools.append("browser-audit.v1")
        if agent_code in {"user-evidence", "business-investment", "geo-policy-trend"}:
            tools.append("public-research-search.v1")
        if os.getenv("LAUNCHSCOPE_USER_VALIDATION_ENABLED", "false").lower() == "true":
            if agent_code == "user-evidence":
                tools.extend([
                    "user-validation-designer.start.v1",
                    "user-validation-designer.submit-step.v1",
                    "user-validation-designer.resume.v1",
                ])
            if agent_code == "evidence-auditor":
                tools.append("user-validation-audit-context.get.v1")
        return tools


__all__ = ["DispatchApplication", "DispatchResult"]
