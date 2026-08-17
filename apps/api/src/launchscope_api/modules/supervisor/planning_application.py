"""Generation-v4 manager-plan validation and control-plane task materialization."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    agent_plan,
    agent_task_ticket,
    agentteams_run_binding,
    budget_reservation,
    evaluation_run,
    outbox_message,
    requirement_brief,
    run_manifest,
    run_status_history,
    skill_version,
    stage,
    task,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.modules.evaluation.clarification_application import (
    pause_run_for_clarification,
    record_information_requests,
)
from launchscope_api.modules.evaluation.intake_application import IntakeValidationError
from launchscope_api.modules.evaluation.model_capability import configured_model_ids, model_usage_ledger_mode
from launchscope_api.modules.evaluation.task_dispatch import (
    enqueue_ready_tasks,
    provider_cost_mode,
    provider_usage_required,
)
from launchscope_api.modules.evidence.mcp_application import configured_authorized_case_urls
from launchscope_api.modules.evidence.tool_limits import BROWSER_CALLS_PER_TASK, SEARCH_QUERIES_PER_TASK
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_domain.value_objects import TenantScope
from launchscope_orchestrator.agentteams_bridge import InformationRequestV1
from launchscope_orchestrator.manifest_loader import AgentManifestLoader
from launchscope_skills import SkillRegistry

from .intake_application import supervisor_1p4_enabled
from .material_routing import persist_task_scopes, scopes_for_task_key, validate_material_scopes

_ROOT = Path(__file__).resolve().parents[6]
_DOMAIN_AGENTS = frozenset({"user-evidence", "product-engineering", "business-investment"})
_PROFILE_BY_MODE = {
    "FULL_POTENTIAL": "full-potential",
    "INVESTMENT_REVIEW": "investment-review",
    "LAUNCH_REVIEW": "launch-review",
    "USER_VALIDATION": "user-validation",
}
_SKILL_BY_AGENT = {
    "user-evidence": "user-validation-designer",
    "product-engineering": "browser-product-audit",
    "business-investment": "business-investment-assessment",
}
_SKILL_VERSION_BY_AGENT = {
    "user-evidence": "1.0.5",
    "product-engineering": "1.0",
    "business-investment": "1.0",
}
_REPORT_V22_SKILL_BY_AGENT = {
    "user-evidence": "user-validation-designer",
    "product-engineering": "product-technical-audit",
    "business-investment": "business-investment-assessment",
}
_REPORT_V22_SKILL_VERSION_BY_AGENT = {
    "user-evidence": "1.1.0",
    "product-engineering": "1.0.0",
    "business-investment": "2.0.0",
}
_UNSTARTED_STATUSES = frozenset({"PENDING", "READY"})


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _normalized_task_budget_suggestions(document: Mapping[str, Any]) -> tuple[Decimal, ...]:
    remaining = Decimal(str(document["budget_suggestion"]))
    normalized: list[Decimal] = []
    for item in document["tasks"]:
        requested = Decimal(str(item["budget_suggestion"]))
        accepted = min(requested, max(remaining, Decimal(0)))
        normalized.append(accepted)
        remaining -= accepted
    return tuple(normalized)


def _registered_skill_version_id(session: Session, skill_code: str, version: str) -> UUID:
    version_id = session.execute(
        select(skill_version.c.id).where(
            skill_version.c.skill_code == skill_code,
            skill_version.c.version == version,
        )
    ).scalar_one_or_none()
    if version_id is None:
        raise RuntimeError(f"registered Skill version is missing: {skill_code}@{version}")
    return version_id


def _explicit_clarification_gate(document: Mapping[str, Any]) -> InformationRequestV1 | None:
    validation_tasks = document.get("validation_tasks")
    if not isinstance(validation_tasks, list):
        return None
    for item in validation_tasks:
        if not isinstance(item, Mapping):
            continue
        task_key = str(item.get("task_key") or "").strip().lower()
        action = str(item.get("description") or item.get("user_action") or "").strip()
        outcome = str(item.get("expected_observable_outcome") or "").strip()
        action_lower = action.lower()
        explicit_key = task_key.startswith("clarify_") or "required_clarification" in task_key
        explicit_gate = "must be clarified before" in action_lower or ("必须" in action and "澄清" in action)
        if not explicit_key or not explicit_gate or "WAITING_FOR_USER" not in outcome:
            continue
        match = re.search(r"ask(?: exactly)? one necessary question:\s*([^?]+\?)", action, re.IGNORECASE)
        if "region" in task_key or "market" in action_lower:
            field = "target_region"
            fallback = "Which target market should be evaluated: US or EU?"
            dimension = "USER_USAGE"
        elif "payer" in task_key or "payment" in task_key:
            field = "payer"
            fallback = "Who is the payer for this evaluation?"
            dimension = "BUSINESS_INVESTMENT"
        else:
            field = "required_clarification"
            fallback = "What missing fact should be used before this task continues?"
            dimension = "USER_USAGE"
        return InformationRequestV1(
            field=field,
            question=match.group(1).strip() if match else fallback,
            why_blocked=action[:1000],
            dimension=dimension,
        )
    return None


class ManagerPlanValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AcceptedPlan:
    plan_id: UUID
    plan_version: int
    task_ids: tuple[UUID, ...]
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class PlanningStartResult:
    run_id: UUID
    manifest_sha256: str
    planning_task_id: UUID


class ManagerPlanValidator:
    def __init__(self, *, budget_cap: Decimal = Decimal("20"), deadline_cap_seconds: int = 3600) -> None:
        if budget_cap <= 0 or deadline_cap_seconds <= 0:
            raise ValueError("plan caps must be positive")
        self._budget_cap = budget_cap
        self._deadline_cap = deadline_cap_seconds
        self._schemas = {
            "1.0": json.loads((_ROOT / "packages/contracts/manager/manager-plan.v1.json").read_text(encoding="utf-8")),
            "2.0": json.loads((_ROOT / "packages/contracts/manager/manager-plan.v2.json").read_text(encoding="utf-8")),
        }
        self._profiles = {
            mode: json.loads(
                (_ROOT / f"packages/contracts/score/profiles/{profile_id}.v1.json").read_text(encoding="utf-8")
            )
            for mode, profile_id in _PROFILE_BY_MODE.items()
        }
        self._tools = {
            generation: {
                code: frozenset(
                    yaml.safe_load(
                        (
                            _ROOT
                            / "packages/contracts"
                            / ("manager/agents" if generation == "v5" else "agents")
                            / f"{code}.{generation}.yaml"
                        ).read_text(encoding="utf-8")
                    )["allowed_tools"]
                )
                for code in _DOMAIN_AGENTS
            }
            for generation in ("v4", "v5")
        }

    def validate(self, document: dict[str, Any], *, report_v2: bool | None = None) -> None:
        schema_version = str(document.get("schema_version") or "")
        schema = self._schemas.get(schema_version)
        if schema is None:
            raise ManagerPlanValidationError("manager plan schema version is unsupported")
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
            key=lambda item: item.json_path,
        )
        if errors:
            raise ManagerPlanValidationError(
                f"ManagerPlanV{'2' if schema_version == '2.0' else '1'} contract violation at {errors[0].json_path}"
            )
        mode = str(document["evaluation_mode"])
        profile = self._profiles[mode]
        expected_ref = f"score-profile:{profile['profile_id']}@{profile['version']}"
        if document["score_profile_ref"] != expected_ref:
            raise ManagerPlanValidationError("score profile does not match evaluation mode")
        tasks = document["tasks"]
        task_agents = [str(item["target_agent"]) for item in tasks]
        if len(task_agents) != len(set(task_agents)):
            raise ManagerPlanValidationError("a domain Agent may appear only once in a plan")
        if mode == "FULL_POTENTIAL" and set(task_agents) != _DOMAIN_AGENTS:
            raise ManagerPlanValidationError("full-potential plans require all three independent domain Agents")
        required_agents = set(profile["required_agents"])
        if not required_agents.issubset(task_agents):
            raise ManagerPlanValidationError("the plan omitted a profile-required Agent")
        trimmed = {str(item["agent_code"]) for item in document["trimmed_domains"]}
        if trimmed != _DOMAIN_AGENTS.difference(task_agents):
            raise ManagerPlanValidationError("trimmed_domains must explain every and only omitted domain")
        if any(bool(item["required"]) != (item["target_agent"] in required_agents) for item in tasks):
            raise ManagerPlanValidationError("task required flags must be controlled by the score profile")
        total_budget = sum(Decimal(str(item["budget_suggestion"])) for item in tasks)
        suggested_budget = Decimal(str(document["budget_suggestion"]))
        rounding_excess = total_budget - suggested_budget
        if suggested_budget > self._budget_cap or rounding_excess > Decimal("0.01"):
            raise ManagerPlanValidationError("manager budget suggestions exceed the control-plane cap")
        if document["deadline_suggestion_seconds"] > self._deadline_cap:
            raise ManagerPlanValidationError("manager deadline suggestion exceeds the control-plane cap")
        for item in tasks:
            agent = str(item["target_agent"])
            if item["dependencies"]:
                raise ManagerPlanValidationError("first-round domain tasks must remain mutually isolated")
            generation = "v5" if schema_version == "2.0" else "v4"
            if not set(item["tool_policy"]).issubset(self._tools[generation][agent]):
                raise ManagerPlanValidationError(f"{agent} requested a tool outside its {generation} identity")
            if int(item["deadline_seconds"]) > min(self._deadline_cap, document["deadline_suggestion_seconds"]):
                raise ManagerPlanValidationError("task deadline exceeds the accepted plan deadline")


class ManagerPlanningApplication:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        validator: ManagerPlanValidator | None = None,
    ) -> None:
        self._sessions = sessions
        self._validator = validator or ManagerPlanValidator()

    def start_planning(self, actor: Actor, run_id: UUID) -> PlanningStartResult:
        if not supervisor_1p4_enabled():
            raise IntakeValidationError("supervisor 1+4 generation is disabled")
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            run = (
                session.execute(
                    select(evaluation_run)
                    .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if run is None:
                raise NotFoundError("run was not found")
            existing = (
                session.execute(
                    select(run_manifest.c.manifest_sha256, run_manifest.c.frozen_config).where(
                        run_manifest.c.tenant_id == actor.tenant_id, run_manifest.c.run_id == run_id
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                existing_generation = existing["frozen_config"].get("architecture_generation")
                if existing_generation not in {
                    "supervisor-1p4-v1",
                    "supervisor-1p4-material-routing-v2",
                    "supervisor-1p4-report-v22",
                    "supervisor-1p4-report-v3",
                }:
                    raise ManagerPlanValidationError("an existing legacy Run cannot switch architecture generation")
                manager_ref = (
                    "evaluation-manager@6.0"
                    if existing_generation in {"supervisor-1p4-report-v22", "supervisor-1p4-report-v3"}
                    else "evaluation-manager@5.0"
                    if existing_generation == "supervisor-1p4-material-routing-v2"
                    else "evaluation-manager@4.0"
                )
                planning_task_id = session.execute(
                    select(task.c.id).where(
                        task.c.tenant_id == actor.tenant_id,
                        task.c.run_id == run_id,
                        task.c.stage_code == "LEADER_PLANNING",
                        task.c.agent_identity_ref == manager_ref,
                    )
                ).scalar_one()
                return PlanningStartResult(run_id, existing["manifest_sha256"], planning_task_id)
            architecture_generation = (run.get("state_flags") or {}).get("architecture_generation")
            if architecture_generation not in {
                "supervisor-1p4-v1",
                "supervisor-1p4-material-routing-v2",
                "supervisor-1p4-report-v22",
                "supervisor-1p4-report-v3",
            }:
                raise ManagerPlanValidationError(
                    "legacy PLANNED Runs are read-only; create a new generation-v4 evaluation"
                )
            if run["status"] != "PLANNED":
                raise ManagerPlanValidationError("generation-v4 planning start requires a PLANNED Run")
            brief = (
                session.execute(
                    select(requirement_brief)
                    .where(
                        requirement_brief.c.tenant_id == actor.tenant_id,
                        requirement_brief.c.product_version_id == run["product_version_id"],
                        requirement_brief.c.status == "READY_FOR_PLANNING",
                    )
                    .order_by(requirement_brief.c.revision.desc())
                )
                .mappings()
                .first()
            )
            if brief is None:
                raise ManagerPlanValidationError("generation-v4 planning requires a confirmed RequirementBrief")
            flags = run.get("state_flags") or {}
            locale = "en" if flags.get("locale") == "en" else "zh-CN"
            report_v3 = architecture_generation == "supervisor-1p4-report-v3"
            report_v2 = architecture_generation in {"supervisor-1p4-report-v22", "supervisor-1p4-report-v3"}
            material_v2 = architecture_generation in {
                "supervisor-1p4-material-routing-v2",
                "supervisor-1p4-report-v22",
                "supervisor-1p4-report-v3",
            }
            manifest = self._run_manifest(
                str(brief["document"]["evaluation_mode"]),
                locale,
                material_v2=material_v2,
                report_v2=report_v2,
                report_v3=report_v3,
            )
            manifest_sha = hashlib.sha256(_canonical(manifest)).hexdigest()
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
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    category="run_total",
                    currency="USD",
                    limit_amount=Decimal("20"),
                    reserved_amount=Decimal("20"),
                    consumed_amount=Decimal("0"),
                    released_amount=Decimal("0"),
                    status="RESERVED",
                    idempotency_key=f"v4-planning:{run_id}:budget",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.execute(
                agentteams_run_binding.insert().values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    agentteams_version="v1.2.0",
                    team_name=(
                        "launchscope-potential-review-v5-operational"
                        if report_v2 or material_v2
                        else "launchscope-potential-review-v4-operational"
                    ),
                    binding_status="PENDING_MANAGER_ACK",
                    created_at=now,
                    updated_at=now,
                )
            )
            stage_id, planning_task_id = uuid4(), uuid4()
            manager_skill_code = "launchscope-evaluation-manager-handoff-v1"
            manager_skill_version = "1.0"
            manager_skill_version_id = _registered_skill_version_id(session, manager_skill_code, manager_skill_version)
            session.execute(
                stage.insert().values(
                    id=stage_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    code="LEADER_PLANNING",
                    ordinal=1,
                    status="READY",
                    started_at=None,
                    completed_at=None,
                )
            )
            session.execute(
                task.insert().values(
                    id=planning_task_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    stage_id=stage_id,
                    agent_identity_id=None,
                    skill_version_id=manager_skill_version_id,
                    stage_code="LEADER_PLANNING",
                    agent_identity_ref=(
                        "evaluation-manager@6.0"
                        if report_v2
                        else "evaluation-manager@5.0"
                        if material_v2
                        else "evaluation-manager@4.0"
                    ),
                    skill_ref=manager_skill_code,
                    skill_version=manager_skill_version,
                    status="READY",
                    lease_token=None,
                    idempotency_key=f"v4-planning:{run_id}:manager",
                    dependencies=[],
                    tool_allowlist=["launchscope-context.get.v2" if material_v2 else "launchscope-context.get.v1"],
                    budget_slice={"currency": "USD", "max": "0"},
                    timeout_seconds=3600,
                    success_condition={"schema": "ManagerPlanV2" if material_v2 else "ManagerPlanV1"},
                    evidence_requirement="plan is a proposal validated and materialized by the control plane",
                    required=True,
                    correction_attempts=0,
                    transient_retries=0,
                    dispatch_epoch=0,
                    last_failure_class=None,
                    last_error=None,
                    side_effect_started=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.execute(
                update(evaluation_run)
                .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                .values(
                    status="RUNNING",
                    current_stage="LEADER_PLANNING",
                    state_flags={**(run.get("state_flags") or {}), "dispatch_pending": True},
                    updated_at=now,
                )
            )
            session.execute(
                run_status_history.insert().values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    from_status="PLANNED",
                    to_status="RUNNING",
                    reason="generation-v4 manager planning dispatch committed",
                    failure_class=None,
                    occurred_at=now,
                )
            )
            if enqueue_ready_tasks(session, actor.tenant_id, run_id, "LEADER_PLANNING") != 1:
                raise RuntimeError("generation-v4 start must enqueue exactly one manager planning task")
            return PlanningStartResult(run_id, manifest_sha, planning_task_id)

    @staticmethod
    def _run_manifest(
        evaluation_mode: str,
        locale: str = "zh-CN",
        *,
        material_v2: bool = False,
        report_v2: bool = False,
        report_v3: bool = False,
    ) -> dict[str, Any]:
        profile_id = _PROFILE_BY_MODE[evaluation_mode]
        profile_path = _ROOT / (
            "packages/contracts/score/profiles/full-potential.v2.json"
            if report_v2 and profile_id == "full-potential"
            else f"packages/contracts/score/profiles/{profile_id}.v1.json"
        )
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        contract_paths = {
            "requirement_brief": "intake/requirement-brief.v1.json",
            "manager_plan": "manager/manager-plan.v2.json" if material_v2 else "manager/manager-plan.v1.json",
            "task_ticket": "tasks/agent-task-ticket.v4.json" if material_v2 else "tasks/agent-task-ticket.v3.json",
            "handoff": "handoffs/agent-handoff.v3.json",
            "audit_request": "audit/audit-request.v3.json",
            "audit_result": "audit/audit-result.v3.json",
            "score_profile": "score/score-profile.v1.json",
            "manager_synthesis": "manager/manager-synthesis.v1.json",
            "run_manifest": "run-manifest/run-manifest.v4.json",
        }
        if material_v2:
            contract_paths.update(
                {
                    "material_manifest": "manager/material/material-manifest.v1.json",
                    "material_unit": "manager/material/material-unit.v1.json",
                    "material_selection": "manager/material/material-selection.v1.json",
                    "run_manifest": "manager/run-manifest.v5.json",
                }
            )
        if report_v2:
            contract_paths.update(
                {
                    "audit_result": "audit/audit-result.v4.json",
                    "score_profile": "score/score-profile.v2.json",
                    "manager_synthesis": "manager/manager-synthesis.v2.json",
                    "citation_source": "reports/citation-source.v1.json",
                    "report_comparison": "reports/report-comparison.v1.json",
                    "specialist_report": "reports/specialist-report.v2.json",
                    "supervisor_report": "reports/supervisor-report.v2.json",
                    "run_manifest": "manager/run-manifest.v6.json",
                }
            )
        if report_v3:
            contract_paths.update(
                {
                    "manager_synthesis_input": "manager/manager-synthesis.v2.json",
                    "specialist_report_input": "reports/specialist-report.v2.json",
                    "specialist_report": "reports/specialist-report.v3.json",
                    "supervisor_report": "reports/supervisor-report.v3.json",
                    "run_manifest": "manager/run-manifest.v7.json",
                }
            )
        contracts = {
            code: {
                "version": Path(relative).stem.split(".")[-1],
                "sha256": hashlib.sha256((_ROOT / "packages/contracts" / relative).read_bytes()).hexdigest(),
            }
            for code, relative in contract_paths.items()
        }
        agents = {
            item.code: {"version": item.version, "sha256": item.content_sha256}
            for item in AgentManifestLoader().load_all("v6" if report_v2 else "v5" if material_v2 else "v4")
        }
        manifest = {
            "schema_version": "7.0" if report_v3 else "6.0" if report_v2 else "5.0" if material_v2 else "4.0",
            "architecture_generation": (
                "supervisor-1p4-report-v3"
                if report_v3
                else "supervisor-1p4-report-v22"
                if report_v2
                else "supervisor-1p4-material-routing-v2"
                if material_v2
                else "supervisor-1p4-v1"
            ),
            "feature_flag": (
                "LAUNCHSCOPE_REPORT_V3_ENABLED"
                if report_v3
                else "LAUNCHSCOPE_REPORT_V2_ENABLED"
                if report_v2
                else "LAUNCHSCOPE_MATERIAL_ROUTING_V2_ENABLED"
                if material_v2
                else "LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED"
            ),
            "report_preferences": {
                "locale": "en" if locale == "en" else "zh-CN",
                "audience": "student",
                "tone": "clear_concise_practical",
            },
            "physical_topology": {
                "worker_count": 5,
                "leader": "evaluation-manager",
                "workers": ["user-evidence", "product-engineering", "business-investment", "evidence-auditor"],
                "peer_mentions": False,
            },
            "agent_contract_generation": "v6" if report_v2 else "v5" if material_v2 else "v4",
            "agents": agents,
            "contracts": contracts,
            "score_profile": {
                "version": profile["version"],
                "sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
            },
            **(
                {
                    "report_profile": {
                        "version": "3.0" if report_v3 else "2.0",
                        "sha256": contracts["supervisor_report"]["sha256"],
                    }
                }
                if report_v2
                else {}
            ),
            "skills": (
                {
                    item.skill_code: {"version": item.version, "sha256": item.content_sha256}
                    for item in SkillRegistry().load_report_v22()
                }
                if report_v2
                else {}
            ),
            "tools": {
                ("launchscope-context.get.v2" if material_v2 else "launchscope-context.get.v1"): (
                    "2.0" if material_v2 else "1.0"
                ),
                **({"material.read.v1": "1.0"} if material_v2 else {}),
                "browser-audit.v1": "1.0",
                "public-research-search.v1": "1.0",
                "repository.read.v1": "1.0",
                "user-validation-audit-context.get.v1": "1.0",
            },
            "research_targets": {"authorized_urls": list(configured_authorized_case_urls())},
            "budget": {"currency": "USD", "limit": "20.00", "manager_may_modify": False},
            "model_pricing": {
                "cost_mode": provider_cost_mode(),
                "input_usd_per_million_tokens": os.getenv("LAUNCHSCOPE_MODEL_INPUT_USD_PER_MILLION"),
                "output_usd_per_million_tokens": os.getenv("LAUNCHSCOPE_MODEL_OUTPUT_USD_PER_MILLION"),
                "required_before_submission": provider_usage_required(),
            },
            "model_accounting": {"mode": model_usage_ledger_mode()},
            "model_runtime": {
                "allowed_model_ids": configured_model_ids(),
                "max_output_tokens_per_call": int(os.getenv("LAUNCHSCOPE_MODEL_MAX_OUTPUT_TOKENS", "32768")),
            },
            "limits": {
                "model_calls": 512 if report_v2 else 256,
                "input_tokens": 25_000_000 if report_v2 else 5_000_000,
                "output_tokens": 1_000_000 if report_v2 else 500_000,
                "search_queries": SEARCH_QUERIES_PER_TASK,
                "browser_calls_per_task": BROWSER_CALLS_PER_TASK,
                "browser_seconds": 600,
                "task_timeout_seconds": 3600,
                "targeted_remediation_rounds": 1,
                "reaudit_rounds": 1,
                **({"material_read_units": 8, "material_read_bytes": 65536} if material_v2 else {}),
                **({"report_body_bytes": 2097152} if report_v2 else {}),
            },
            "failure_policy": {
                "SUBMISSION_UNKNOWN": "NEEDS_ATTENTION_NO_RETRY",
                "USAGE_UNKNOWN": "NEEDS_ATTENTION_NO_RETRY",
                "BILLING_UNKNOWN": "NEEDS_ATTENTION_NO_RETRY",
                "PAID_TIMEOUT": "NEEDS_ATTENTION_NO_RETRY",
                **({"MATERIAL_INTEGRITY_FAILED": "NEEDS_ATTENTION_NO_RETRY"} if material_v2 else {}),
                **(
                    {
                        "REPORT_INTEGRITY_FAILED": "NEEDS_ATTENTION_NO_RETRY",
                        "EXPORT_AMBIGUOUS": "NEEDS_ATTENTION_NO_RETRY",
                    }
                    if report_v2
                    else {}
                ),
            },
        }
        return manifest

    def accept_and_materialize(
        self,
        actor: Actor,
        run_id: UUID,
        planning_task_id: UUID,
        document: dict[str, Any],
    ) -> AcceptedPlan:
        if not supervisor_1p4_enabled():
            raise IntakeValidationError("supervisor 1+4 generation is disabled")
        digest = hashlib.sha256(_canonical(document)).hexdigest()
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            run = (
                session.execute(
                    select(evaluation_run)
                    .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if run is None:
                raise NotFoundError("run was not found")
            architecture_generation = (run.get("state_flags") or {}).get("architecture_generation")
            report_v2 = architecture_generation in {"supervisor-1p4-report-v22", "supervisor-1p4-report-v3"}
            self._validator.validate(document, report_v2=report_v2)
            if UUID(str(document["run_id"])) != run_id:
                raise ManagerPlanValidationError("plan run_id does not match the target Run")
            brief = (
                session.execute(
                    select(requirement_brief).where(
                        requirement_brief.c.tenant_id == actor.tenant_id,
                        requirement_brief.c.id == UUID(str(document["brief_id"])),
                        requirement_brief.c.product_version_id == run["product_version_id"],
                        requirement_brief.c.status == "READY_FOR_PLANNING",
                    )
                )
                .mappings()
                .first()
            )
            if brief is None or brief["document"]["evaluation_mode"] != document["evaluation_mode"]:
                raise ManagerPlanValidationError("plan must reference the confirmed matching RequirementBrief")
            material_v2 = str(document.get("schema_version")) == "2.0"
            if material_v2 != (
                architecture_generation
                in {"supervisor-1p4-material-routing-v2", "supervisor-1p4-report-v22", "supervisor-1p4-report-v3"}
            ):
                raise ManagerPlanValidationError("manager plan generation does not match the frozen Run architecture")
            resolved_scopes = (
                validate_material_scopes(
                    session,
                    actor.tenant_id,
                    UUID(str(run["product_version_id"])),
                    document,
                )
                if material_v2
                else {}
            )
            planning_task = (
                session.execute(
                    select(task.c.id, task.c.stage_id).where(
                        task.c.tenant_id == actor.tenant_id,
                        task.c.id == planning_task_id,
                        task.c.run_id == run_id,
                        task.c.agent_identity_ref
                        == (
                            "evaluation-manager@6.0"
                            if report_v2
                            else "evaluation-manager@5.0"
                            if material_v2
                            else "evaluation-manager@4.0"
                        ),
                    )
                )
                .mappings()
                .first()
            )
            if planning_task is None:
                raise ManagerPlanValidationError("plan must come from the generation-v4 manager planning task")
            duplicate = session.execute(
                select(agent_plan.c.id, agent_plan.c.plan_version).where(
                    agent_plan.c.tenant_id == actor.tenant_id,
                    agent_plan.c.run_id == run_id,
                    agent_plan.c.plan_sha256 == digest,
                )
            ).first()
            if duplicate is not None:
                duplicate_task_ids = tuple(
                    session.execute(
                        select(agent_task_ticket.c.task_id).where(
                            agent_task_ticket.c.tenant_id == actor.tenant_id,
                            agent_task_ticket.c.plan_id == duplicate.id,
                        )
                    ).scalars()
                )
                return AcceptedPlan(duplicate.id, duplicate.plan_version, duplicate_task_ids, duplicate=True)
            prior = (
                session.execute(
                    select(agent_plan)
                    .where(
                        agent_plan.c.tenant_id == actor.tenant_id,
                        agent_plan.c.run_id == run_id,
                        agent_plan.c.status == "ACCEPTED",
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            expected_version = 1 if prior is None else int(prior["plan_version"]) + 1
            if int(document["plan_version"]) != expected_version:
                raise ManagerPlanValidationError("plan_version is not the next accepted version")
            plan_id = UUID(str(document["plan_id"]))
            if prior is None:
                if document["supersedes_plan_id"] is not None or document["replan_reason"] is not None:
                    raise ManagerPlanValidationError("an initial plan cannot claim a superseded plan")
                retained_tasks: dict[str, UUID] = {}
            else:
                if UUID(str(document["supersedes_plan_id"])) != prior["id"] or not document["replan_reason"]:
                    raise ManagerPlanValidationError("a replan must identify and explain the accepted prior plan")
                retained_tasks = self._assert_replan_only_changes_unstarted(
                    session, actor.tenant_id, dict(prior), document
                )
                session.execute(
                    update(agent_plan)
                    .where(agent_plan.c.tenant_id == actor.tenant_id, agent_plan.c.id == prior["id"])
                    .values(status="SUPERSEDED", decided_at=now)
                )
                mutable_ticket_ids = tuple(
                    session.execute(
                        select(agent_task_ticket.c.id)
                        .join(
                            task,
                            (task.c.tenant_id == agent_task_ticket.c.tenant_id)
                            & (task.c.id == agent_task_ticket.c.task_id),
                        )
                        .where(
                            agent_task_ticket.c.tenant_id == actor.tenant_id,
                            agent_task_ticket.c.plan_id == prior["id"],
                            agent_task_ticket.c.status == "PREPARED",
                            task.c.status.in_(_UNSTARTED_STATUSES),
                        )
                    ).scalars()
                )
                mutable_task_ids = tuple(
                    session.execute(
                        select(agent_task_ticket.c.task_id).where(
                            agent_task_ticket.c.tenant_id == actor.tenant_id,
                            agent_task_ticket.c.id.in_(mutable_ticket_ids),
                        )
                    ).scalars()
                )
                session.execute(
                    update(outbox_message)
                    .where(
                        outbox_message.c.tenant_id == actor.tenant_id,
                        outbox_message.c.idempotency_key.in_(
                            tuple(f"task-ready:{run_id}:{task_id}:0" for task_id in mutable_task_ids)
                        ),
                        outbox_message.c.publish_status.in_(("PENDING", "HELD")),
                    )
                    .values(publish_status="CANCELLED", last_error="superseded by accepted manager replan")
                )
                session.execute(
                    update(task)
                    .where(task.c.tenant_id == actor.tenant_id, task.c.id.in_(mutable_task_ids))
                    .values(status="CANCELLED", updated_at=now)
                )
                session.execute(
                    update(agent_task_ticket)
                    .where(
                        agent_task_ticket.c.tenant_id == actor.tenant_id,
                        agent_task_ticket.c.id.in_(mutable_ticket_ids),
                    )
                    .values(status="EXPIRED")
                )
            session.execute(
                agent_plan.insert().values(
                    id=plan_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    planning_task_id=planning_task_id,
                    dispatch_epoch=0,
                    plan_version=expected_version,
                    evaluation_mode=document["evaluation_mode"],
                    raw_plan=document,
                    plan_sha256=digest,
                    status="ACCEPTED",
                    matrix_event_id=None,
                    rejection_code=None,
                    decision_reason="validated and materialized by the control plane",
                    supersedes_plan_id=None if prior is None else prior["id"],
                    created_at=now,
                    decided_at=now,
                )
            )
            stage_id = self._domain_stage(session, actor.tenant_id, run_id, now)
            task_ids = self._materialize_tasks(
                session,
                actor.tenant_id,
                run_id,
                plan_id,
                expected_version,
                stage_id,
                document,
                now,
                retained_tasks,
                material_v2,
                report_v2,
                resolved_scopes,
            )
            clarification = _explicit_clarification_gate(brief["document"])
            if clarification is not None:
                asking_task_id = session.execute(
                    select(task.c.id).where(
                        task.c.tenant_id == actor.tenant_id,
                        task.c.id.in_(task_ids),
                        task.c.agent_identity_ref
                        == (
                            "user-evidence@6.0"
                            if report_v2
                            else "user-evidence@5.0"
                            if material_v2
                            else "user-evidence@4.0"
                        ),
                    )
                ).scalar_one()
                record_information_requests(
                    session,
                    actor.tenant_id,
                    run_id,
                    asking_task_id,
                    ("user-evidence@6.0" if report_v2 else "user-evidence@5.0" if material_v2 else "user-evidence@4.0"),
                    [clarification],
                    now,
                )
            ready_result = session.execute(
                update(task)
                .where(
                    task.c.tenant_id == actor.tenant_id,
                    task.c.id.in_(task_ids),
                    task.c.status == "PENDING",
                )
                .values(status="READY", updated_at=now)
            )
            ready_count = int(getattr(ready_result, "rowcount", 0) or 0)
            if enqueue_ready_tasks(session, actor.tenant_id, run_id, "DOMAIN_REVIEW") != ready_count:
                raise RuntimeError("every newly materialized generation-v4 domain Task must be enqueued")
            session.execute(
                update(task)
                .where(task.c.tenant_id == actor.tenant_id, task.c.id == planning_task_id)
                .values(status="SUCCEEDED", updated_at=now)
            )
            session.execute(
                update(stage)
                .where(stage.c.tenant_id == actor.tenant_id, stage.c.id == planning_task["stage_id"])
                .values(status="COMPLETED", completed_at=now)
            )
            session.execute(
                update(evaluation_run)
                .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                .values(current_stage="DOMAIN_REVIEW", updated_at=now)
            )
            if clarification is not None:
                pause_run_for_clarification(
                    session,
                    actor.tenant_id,
                    run_id,
                    now,
                    "Confirmed validation task requires one user-owned fact before external research",
                )
            return AcceptedPlan(plan_id, expected_version, tuple(task_ids))

    @staticmethod
    def _assert_replan_only_changes_unstarted(
        session: Session, tenant_id: UUID, prior: Mapping[str, Any], document: dict[str, Any]
    ) -> dict[str, UUID]:
        prior_rows = (
            session.execute(
                select(
                    task.c.id,
                    task.c.status,
                    task.c.timeout_seconds,
                    agent_task_ticket.c.target_agent,
                    agent_task_ticket.c.public_summary,
                )
                .join(
                    agent_task_ticket,
                    (agent_task_ticket.c.tenant_id == task.c.tenant_id) & (agent_task_ticket.c.task_id == task.c.id),
                )
                .where(agent_task_ticket.c.tenant_id == tenant_id, agent_task_ticket.c.plan_id == prior["id"])
            )
            .mappings()
            .all()
        )
        proposed = {str(item["target_agent"]): item for item in document["tasks"]}
        retained: dict[str, UUID] = {}
        for row in prior_rows:
            if row["status"] == "PENDING":
                continue
            if row["status"] == "READY":
                publish_status = session.execute(
                    select(outbox_message.c.publish_status).where(
                        outbox_message.c.tenant_id == tenant_id,
                        outbox_message.c.idempotency_key == f"task-ready:{prior['run_id']}:{row['id']}:0",
                        outbox_message.c.event_type == "evaluation.task.ready.v1",
                    )
                ).scalar_one_or_none()
                if publish_status in {"PENDING", "HELD"}:
                    continue
            old = row["public_summary"]
            new = proposed.get(str(row["target_agent"]))
            if (
                new is None
                or int(new["deadline_seconds"]) != int(row["timeout_seconds"])
                or any(
                    new[field] != old[field]
                    for field in (
                        "input_refs",
                        "analysis_dimensions",
                        "region_scope",
                        "as_of",
                        "tool_policy",
                        "success_conditions",
                        "required",
                    )
                )
                or (new.get("material_scope") or []) != (old.get("material_scope") or [])
            ):
                raise ManagerPlanValidationError("replanning cannot remove or change a started task")
            retained[str(row["target_agent"])] = row["id"]
        return retained

    @staticmethod
    def _domain_stage(session: Session, tenant_id: UUID, run_id: UUID, now: datetime) -> UUID:
        existing = session.execute(
            select(stage.c.id).where(
                stage.c.tenant_id == tenant_id,
                stage.c.run_id == run_id,
                stage.c.code == "DOMAIN_REVIEW",
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        stage_id = uuid4()
        ordinal = (
            int(
                session.execute(
                    select(func.coalesce(func.max(stage.c.ordinal), 0)).where(
                        stage.c.tenant_id == tenant_id, stage.c.run_id == run_id
                    )
                ).scalar_one()
            )
            + 1
        )
        session.execute(
            stage.insert().values(
                id=stage_id,
                tenant_id=tenant_id,
                run_id=run_id,
                code="DOMAIN_REVIEW",
                ordinal=ordinal,
                status="RUNNING",
                started_at=now,
                completed_at=None,
            )
        )
        return stage_id

    @staticmethod
    def _materialize_tasks(
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        plan_id: UUID,
        plan_version: int,
        stage_id: UUID,
        document: dict[str, Any],
        now: datetime,
        retained_tasks: dict[str, UUID],
        material_v2: bool,
        report_v2: bool,
        resolved_scopes: dict[str, tuple[dict[str, object], ...]],
    ) -> list[UUID]:
        task_ids: list[UUID] = []
        normalized_budgets = _normalized_task_budget_suggestions(document)
        for item, normalized_budget in zip(document["tasks"], normalized_budgets, strict=True):
            task_id = uuid4()
            ticket_id = uuid4()
            agent = str(item["target_agent"])
            if agent in retained_tasks:
                task_ids.append(retained_tasks[agent])
                continue
            deadline = now + timedelta(seconds=int(item["deadline_seconds"]))
            assigned_skill_code = _REPORT_V22_SKILL_BY_AGENT[agent] if report_v2 else _SKILL_BY_AGENT[agent]
            assigned_skill_version = (
                _REPORT_V22_SKILL_VERSION_BY_AGENT[agent] if report_v2 else _SKILL_VERSION_BY_AGENT[agent]
            )
            assigned_skill_version_id = _registered_skill_version_id(
                session, assigned_skill_code, assigned_skill_version
            )
            public_summary = {
                "schema_version": "4.0" if material_v2 else "3.0",
                "ticket_id": str(ticket_id),
                "run_id": str(run_id),
                "task_id": str(task_id),
                "plan_id": str(plan_id),
                "plan_version": plan_version,
                "dispatch_epoch": 0,
                "target_agent": agent,
                "objective": str(item["task_key"]),
                "input_refs": item["input_refs"],
                "analysis_dimensions": item["analysis_dimensions"],
                "region_scope": item["region_scope"],
                "as_of": item["as_of"],
                "tool_policy": list(
                    dict.fromkeys([*item["tool_policy"], *(["material.read.v1"] if material_v2 else [])])
                ),
                "success_conditions": item["success_conditions"],
                "required": item["required"],
                "deadline_at": deadline.isoformat().replace("+00:00", "Z"),
                "report_contract": "specialist-report.v2" if report_v2 else "domain-report-ref.v1",
                "handoff_contract": "agent-handoff.v3",
            }
            scopes = scopes_for_task_key(resolved_scopes, str(item["task_key"])) if material_v2 else ()
            if material_v2:
                public_material_scope = [
                    {
                        "scope_id": scope["scope_id"],
                        "material_id": scope["material_id"],
                        "unit_refs": scope["declared_unit_refs"],
                        "reason": scope["reason"],
                        "required": scope["required"],
                    }
                    for scope in scopes
                ]
                public_summary["material_scope"] = public_material_scope
                public_summary["material_scope_sha256"] = hashlib.sha256(_canonical(public_material_scope)).hexdigest()
            ticket_sha = hashlib.sha256(_canonical(public_summary)).hexdigest()
            session.execute(
                task.insert().values(
                    id=task_id,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    stage_id=stage_id,
                    agent_identity_id=None,
                    skill_version_id=assigned_skill_version_id,
                    stage_code="DOMAIN_REVIEW",
                    agent_identity_ref=f"{agent}@{'6.0' if report_v2 else '5.0' if material_v2 else '4.0'}",
                    skill_ref=assigned_skill_code,
                    skill_version=assigned_skill_version,
                    status="PENDING",
                    lease_token=None,
                    idempotency_key=f"v4:{plan_id}:{item['task_key']}",
                    dependencies=[],
                    tool_allowlist=public_summary["tool_policy"],
                    budget_slice={"suggested_usd": float(normalized_budget)},
                    timeout_seconds=item["deadline_seconds"],
                    success_condition=item["success_conditions"],
                    evidence_requirement="finding evidence, region_scope, as_of, valid_until, and SHA-bound report",
                    required=item["required"],
                    correction_attempts=0,
                    transient_retries=0,
                    dispatch_epoch=0,
                    last_failure_class=None,
                    last_error=None,
                    side_effect_started=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.execute(
                agent_task_ticket.insert().values(
                    id=ticket_id,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    plan_id=plan_id,
                    dispatch_epoch=0,
                    target_agent=agent,
                    ticket_sha256=ticket_sha,
                    public_summary=public_summary,
                    usage_baseline=None,
                    status="PREPARED",
                    expires_at=deadline,
                    created_at=now,
                    delivered_at=None,
                )
            )
            if material_v2:
                persist_task_scopes(session, tenant_id, run_id, task_id, plan_id, scopes, now)
            task_ids.append(task_id)
        return task_ids


__all__ = [
    "AcceptedPlan",
    "ManagerPlanValidationError",
    "ManagerPlanValidator",
    "ManagerPlanningApplication",
    "PlanningStartResult",
]
