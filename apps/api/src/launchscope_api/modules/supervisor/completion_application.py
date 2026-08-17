"""Deterministic scoring, constrained synthesis, and atomic generation-v4 completion."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    agent_plan,
    agent_report_artifact,
    agent_task_ticket,
    conflict_record,
    decision,
    decision_finding,
    evaluation_run,
    evidence,
    evidence_audit,
    evidence_source_locator,
    finding,
    manager_synthesis,
    product_version,
    project,
    project_dossier_snapshot,
    report,
    report_claim_citation,
    run_manifest,
    run_status_history,
    skill_version,
    stage,
    task,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.modules.evaluation.intake_application import IntakeValidationError
from launchscope_api.modules.evaluation.task_dispatch import enqueue_ready_tasks
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_domain.value_objects import TenantScope

from .intake_application import supervisor_1p4_enabled
from .report_comparison import build_report_comparison
from .report_metrics import compute_conclusion_confidence, compute_evidence_coverage
from .report_v2 import SupervisorReportV2Builder, SupervisorReportV2Error
from .report_v3 import SupervisorReportV3Builder, SupervisorReportV3Error

_ROOT = Path(__file__).resolve().parents[6]
_RECOMMENDATIONS = ("PAUSE", "ADJUST", "VALIDATE_FURTHER", "PROCEED")
_DIMENSION_BY_AGENT = {
    "user-evidence": "user_value",
    "product-engineering": "product_capability",
    "business-investment": "investment_potential",
}
_FINAL_TASK_STATUSES = frozenset({"SUCCEEDED", "KNOWN_FAILED", "FAILED"})


class ReportObjectStore(Protocol):
    def head(self, object_key: str) -> Any: ...

    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str: ...


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _cap_recommendation(value: str, cap: str) -> str:
    return _RECOMMENDATIONS[min(_RECOMMENDATIONS.index(value), _RECOMMENDATIONS.index(cap))]


def _apply_canonical_synthesis_evidence_citations(document: dict[str, Any], canonical_by_id: Mapping[str, str]) -> None:
    for citation in document.get("citations", []):
        if citation.get("kind") != "EVIDENCE":
            continue
        reference = str(citation.get("ref", ""))
        evidence_id = reference.removeprefix("evidence:")
        canonical = canonical_by_id.get(evidence_id)
        if canonical is not None:
            citation["ref"] = canonical


def _bind_report_citations(
    document: dict[str, Any],
    citation_bases: list[dict[str, Any]],
    audited: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = copy.deepcopy(document)
    bases_by_id: dict[str, dict[str, Any]] = {}
    for item in citation_bases:
        bases_by_id[str(item["citation_id"])] = item
        for alias in item.get("aliases", []):
            bases_by_id.setdefault(str(alias), item)
    bases_by_evidence: dict[str, list[dict[str, Any]]] = {}
    for item in citation_bases:
        bases_by_evidence.setdefault(str(item["evidence_id"]), []).append(item)
    agent_by_source_claim = {f"claim-{item['id']}": str(item["finding"]["agent_code"]) for item in audited}
    preferred_agent = {
        "USER": "user-evidence",
        "PRODUCT": "product-engineering",
        "INVESTMENT": "business-investment",
    }
    citations: list[dict[str, Any]] = []
    for claim in normalized["claims"]:
        bound_ids: list[str] = []
        unresolved_ids: list[str] = []
        for source_id in claim["citation_ids"]:
            base = bases_by_id.get(str(source_id))
            if base is None:
                raw_evidence_id = str(source_id).removeprefix("citation-")
                try:
                    evidence_id = str(UUID(raw_evidence_id))
                except ValueError:
                    unresolved_ids.append(str(source_id))
                    continue
                candidates = list(bases_by_evidence.get(evidence_id, ()))
                preferred = preferred_agent.get(str(claim["section"]))
                if preferred is not None:
                    matching = [
                        item
                        for item in candidates
                        if agent_by_source_claim.get(str(item["source_claim_id"])) == preferred
                    ]
                    if matching:
                        candidates = matching
                if not candidates:
                    unresolved_ids.append(str(source_id))
                    continue
                rank = {"VERIFIED": 0, "DOWNGRADED": 1, "NEEDS_MORE": 2, "REJECTED": 3}
                base = min(
                    candidates,
                    key=lambda item: (
                        rank.get(str(item["audit_status"]), 4),
                        item["source_locator_id"] is None,
                        str(item["citation_id"]),
                    ),
                )
            label = len(bound_ids) + 1
            final_id = f"citation-{str(claim['claim_id']).removeprefix('claim-')}-{label}"
            bound_ids.append(final_id)
            citations.append(
                {
                    key: value
                    for key, value in {
                        **base,
                        "citation_id": final_id,
                        "claim_id": claim["claim_id"],
                        "label": label,
                    }.items()
                    if key not in {"aliases", "source_claim_id"}
                }
            )
        if unresolved_ids and not bound_ids:
            raise CompletionValidationError("ManagerSynthesisV2 cites an unknown audited Citation")
        claim["citation_ids"] = bound_ids
    return normalized, citations


class CompletionValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DeterministicScore:
    score: float
    coverage: float
    recommendation: str
    dimension_scores: dict[str, float | None]
    caps_applied: tuple[str, ...]
    missing_agents: tuple[str, ...]

    def document(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "coverage": self.coverage,
            "recommendation": self.recommendation,
            "dimension_scores": self.dimension_scores,
            "caps_applied": list(self.caps_applied),
            "missing_agents": list(self.missing_agents),
        }


@dataclass(frozen=True, slots=True)
class ScoringPreparation:
    decision_id: UUID
    synthesis_task_id: UUID
    score: DeterministicScore
    context_ref: dict[str, str]
    version_changes: dict[str, list[str]]
    comparison: dict[str, Any] | None = None
    confidence: dict[str, Any] | None = None
    evidence_coverage: float = 0.0


@dataclass(frozen=True, slots=True)
class CompletionResult:
    decision_id: UUID
    report_id: UUID
    dossier_snapshot_id: UUID
    recommendation: str
    decision_conflict: bool


class DeterministicScoringEngine:
    def score(
        self,
        profile: dict[str, Any],
        audited_findings: list[dict[str, Any]],
        planned_tasks: list[dict[str, Any]],
        *,
        unresolved_conflicts: bool,
    ) -> DeterministicScore:
        planned_agents = {
            str(item["agent"]): item for item in planned_tasks if str(item["agent"]) in _DIMENSION_BY_AGENT
        }
        usable_agents: set[str] = set()
        grouped: dict[str, list[float]] = {agent: [] for agent in planned_agents}
        accepted_count = 0
        requires_valid_evidence = bool(profile.get("coverage_rules", {}).get("requires_valid_evidence", False))
        for item in audited_findings:
            audit_decision = str(item["audit_decision"])
            if audit_decision == "NEEDS_MORE":
                raise CompletionValidationError("deterministic scoring cannot run with unresolved NEEDS_MORE")
            source = item["finding"]
            agent = str(source["agent_code"])
            citation_status = str(item.get("citation_status", ""))
            score_bearing = bool(item.get("score_bearing", not requires_valid_evidence))
            citation_admitted = citation_status in {"VERIFIED", "DOWNGRADED"} or not requires_valid_evidence
            if audit_decision in {"ACCEPTED", "DOWNGRADED"} and score_bearing and citation_admitted:
                accepted_count += 1
                usable_agents.add(agent)
                raw = source["score_input"]
                if raw is not None:
                    factor = 1.0 if audit_decision == "ACCEPTED" else 0.75
                    grouped.setdefault(agent, []).append(float(raw) * 20 * factor)
        denominator = max(len(planned_agents), 1)
        coverage = len(usable_agents.intersection(planned_agents)) / denominator
        missing_agents = tuple(sorted(set(planned_agents).difference(usable_agents)))
        dimension_scores: dict[str, float | None] = {}
        weighted_score = 0.0
        for agent, dimension in _DIMENSION_BY_AGENT.items():
            values = grouped.get(agent, [])
            score = None if not values else round(sum(values) / len(values), 2)
            dimension_scores[dimension] = score
            if score is not None and dimension in profile["weights"]:
                weighted_score += score * float(profile["weights"][dimension])
        evidence_quality = 0.0 if not audited_findings else accepted_count / len(audited_findings) * 100
        dimension_scores["evidence_quality"] = round(evidence_quality, 2)
        weighted_score += evidence_quality * float(profile["weights"].get("evidence_quality", 0))
        score = round(weighted_score, 2)
        thresholds = profile["thresholds"]
        if score >= thresholds["PROCEED"]:
            recommendation = "PROCEED"
        elif score >= thresholds["VALIDATE_FURTHER"]:
            recommendation = "VALIDATE_FURTHER"
        elif score >= thresholds["ADJUST"]:
            recommendation = "ADJUST"
        else:
            recommendation = "PAUSE"
        caps: list[str] = []
        optional_failures = [
            agent
            for agent, item in planned_agents.items()
            if not item["required"] and item["status"] in {"KNOWN_FAILED", "FAILED"}
        ]
        if optional_failures:
            cap = profile["recommendation_caps"]["missing_optional_agent"]
            recommendation = _cap_recommendation(recommendation, cap)
            caps.append(f"missing_optional_agent:{cap}")
        if unresolved_conflicts:
            cap = profile["recommendation_caps"]["unresolved_conflict"]
            recommendation = _cap_recommendation(recommendation, cap)
            caps.append(f"unresolved_conflict:{cap}")
        if coverage < float(profile["coverage_rules"]["proceed_minimum"]):
            cap = profile["recommendation_caps"]["low_coverage"]
            recommendation = _cap_recommendation(recommendation, cap)
            caps.append(f"low_coverage:{cap}")
        return DeterministicScore(
            score,
            round(coverage, 4),
            recommendation,
            dimension_scores,
            tuple(caps),
            missing_agents,
        )


class VersionChangeComparator:
    def compare(self, prior: list[dict[str, Any]], current: list[dict[str, Any]]) -> dict[str, list[str]]:
        def averages(items: list[dict[str, Any]]) -> dict[str, float]:
            values: dict[str, list[float]] = {}
            for item in items:
                source = item["finding"]
                if (
                    source["score_input"] is not None
                    and item["audit_decision"] != "REJECTED"
                    and item.get("score_bearing", True)
                ):
                    values.setdefault(source["agent_code"], []).append(float(source["score_input"]))
            return {key: sum(score_values) / len(score_values) for key, score_values in values.items()}

        old, new = averages(prior), averages(current)
        improved = sorted(agent for agent, value in new.items() if agent in old and value > old[agent] + 0.01)
        unchanged = sorted(agent for agent, value in new.items() if agent in old and abs(value - old[agent]) <= 0.01)
        prior_claims = {item["finding"]["claim"] for item in prior}
        new_risks = sorted(
            item["finding"]["claim"]
            for item in current
            if item["finding"]["claim"] not in prior_claims
            and (item["audit_decision"] == "REJECTED" or item["finding"]["grade"] == "WEAK")
        )
        return {"improved": improved, "unchanged": unchanged, "new_risks": new_risks}


class SupervisorCompletionApplication:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        objects: ReportObjectStore,
        *,
        scoring: DeterministicScoringEngine | None = None,
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._scoring = scoring or DeterministicScoringEngine()
        self._synthesis_schema = json.loads(
            (_ROOT / "packages/contracts/manager/manager-synthesis.v1.json").read_text(encoding="utf-8")
        )
        self._synthesis_v2_schema = json.loads(
            (_ROOT / "packages/contracts/manager/manager-synthesis.v2.json").read_text(encoding="utf-8")
        )

    def prepare_scoring(self, actor: Actor, run_id: UUID) -> ScoringPreparation:
        self._require_enabled()
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            run = self._locked_run(session, actor.tenant_id, run_id)
            manifest = session.execute(
                select(run_manifest.c.frozen_config).where(
                    run_manifest.c.tenant_id == actor.tenant_id,
                    run_manifest.c.run_id == run_id,
                )
            ).scalar_one_or_none()
            report_v2_run = (manifest or {}).get("agent_contract_generation") == "v6"
            if run["current_stage"] != "DETERMINISTIC_SCORING" or run["status"] != "RUNNING":
                raise CompletionValidationError("scoring requires the audited DETERMINISTIC_SCORING state")
            plan = (
                session.execute(
                    select(agent_plan).where(
                        agent_plan.c.tenant_id == actor.tenant_id,
                        agent_plan.c.run_id == run_id,
                        agent_plan.c.status == "ACCEPTED",
                    )
                )
                .mappings()
                .one()
            )
            requested_profile_ref = str(plan["raw_plan"]["score_profile_ref"])
            profile_ref = (
                "score-profile:full-potential@2.0"
                if report_v2_run and requested_profile_ref == "score-profile:full-potential@1.0"
                else requested_profile_ref
            )
            profile = self._load_profile(profile_ref)
            audited = self._audited_findings(session, actor.tenant_id, run_id)
            planned_tasks = self._planned_tasks(session, actor.tenant_id, run_id, plan["id"])
            required_failures = [item for item in planned_tasks if item["required"] and item["status"] != "SUCCEEDED"]
            if required_failures:
                raise CompletionValidationError("a required Agent is not successful; scoring is prohibited")
            unresolved = (
                session.execute(
                    select(conflict_record.c.id)
                    .where(
                        conflict_record.c.tenant_id == actor.tenant_id,
                        conflict_record.c.run_id == run_id,
                        conflict_record.c.resolution_status != "RESOLVED",
                    )
                    .limit(1)
                ).first()
                is not None
            )
            score = self._scoring.score(profile, audited, planned_tasks, unresolved_conflicts=unresolved)
            score_document = score.document()
            score_document["score_profile_ref"] = profile_ref
            comparison = None
            confidence = None
            evidence_coverage = 0.0
            if report_v2_run:
                evidence_coverage = compute_evidence_coverage(profile, audited)
                confidence = compute_conclusion_confidence(
                    profile,
                    audited,
                    evidence_coverage=evidence_coverage,
                    cross_domain_agreement=0.0 if unresolved else 1.0,
                    unresolved_conflicts=unresolved,
                    profile_ref=profile_ref,
                )
                comparison = self._build_comparison_snapshot(
                    session,
                    actor.tenant_id,
                    run,
                    audited,
                    score,
                    profile,
                    profile_ref,
                )
                score_document.update(
                    {
                        "evidence_coverage": evidence_coverage,
                        "confidence_breakdown": confidence,
                        "comparison": comparison,
                    }
                )
            decision_id = uuid4()
            session.execute(
                decision.insert().values(
                    id=decision_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    recommendation=score.recommendation,
                    standard_version=str(profile["version"]),
                    dimension_grades=score_document,
                    hard_blocks=[],
                    supersedes_id=None,
                    created_at=now,
                )
            )
            for item in audited:
                session.execute(
                    decision_finding.insert().values(
                        tenant_id=actor.tenant_id,
                        decision_id=decision_id,
                        finding_id=item["id"],
                        role="EXCLUDED" if item["audit_decision"] == "REJECTED" else "INFORMS",
                    )
                )
            version_changes = self._version_changes(session, actor.tenant_id, run, audited)
            preferences = dict(
                (manifest or {}).get("report_preferences")
                or {
                    "locale": "zh-CN",
                    "audience": "student",
                    "tone": "clear_concise_practical",
                }
            )
            architecture_generation = (manifest or {}).get("architecture_generation")
            agent_version = (
                "6.0"
                if architecture_generation in {"supervisor-1p4-report-v22", "supervisor-1p4-report-v3"}
                else "5.0"
                if architecture_generation == "supervisor-1p4-material-routing-v2"
                else "4.0"
            )
            citation_context: list[dict[str, Any]] = []
            if report_v2_run:
                citation_context, _source_directory, _allowed_evidence_ids = self._report_citation_catalog(
                    session, actor.tenant_id, run_id, audited
                )
            citation_ids_by_claim: dict[str, list[str]] = {}
            for item in citation_context:
                citation_ids_by_claim.setdefault(str(item["source_claim_id"]), []).append(str(item["citation_id"]))
            context = {
                "schema_version": "1.0",
                "run_id": str(run_id),
                "requirement_layer": {
                    "product_version_id": str(run["product_version_id"]),
                    "evaluation_mode": plan["evaluation_mode"],
                },
                "decision_layer": {"decision_id": str(decision_id), **score.document()},
                "audit_layer": [
                    {
                        "finding_id": str(item["id"]),
                        "claim_id": f"claim-{item['id']}",
                        "agent_code": item["finding"]["agent_code"],
                        "claim": item["finding"]["claim"],
                        "audit_decision": item["audit_decision"],
                        "evidence_refs": item["finding"]["evidence_refs"],
                        "citation_ids": citation_ids_by_claim.get(f"claim-{item['id']}", []),
                    }
                    for item in audited
                ],
                "history_layer": version_changes,
                "report_metrics": {
                    "evidence_coverage": evidence_coverage,
                    "confidence_breakdown": confidence,
                    "comparison": comparison,
                },
                "citation_catalog": citation_context,
                "report_preferences": preferences,
            }
            context_body = _canonical(context)
            context_sha = hashlib.sha256(context_body).hexdigest()
            context_key = f"tenant/{actor.tenant_id}/run/{run_id}/synthesis-context/{context_sha}.json"
            if self._objects.put_private(context_key, context_body, "application/json") != context_sha:
                raise CompletionValidationError("object store changed the synthesis context digest")
            synthesis_task_id = self._materialize_synthesis_task(
                session,
                actor.tenant_id,
                run_id,
                dict(plan),
                context_key,
                context_sha,
                preferences,
                agent_version,
                report_v2_run,
                now,
            )
            session.execute(
                update(evaluation_run)
                .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                .values(current_stage="SUPERVISOR_SYNTHESIS", updated_at=now)
            )
            return ScoringPreparation(
                decision_id,
                synthesis_task_id,
                score,
                {"ref": context_key, "sha256": context_sha},
                version_changes,
                comparison,
                confidence,
                evidence_coverage,
            )

    def commit_synthesis_report(
        self,
        actor: Actor,
        run_id: UUID,
        synthesis_task_id: UUID,
        document: dict[str, Any],
    ) -> CompletionResult:
        self._require_enabled()
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            run = self._locked_run(session, actor.tenant_id, run_id)
            manifest = session.execute(
                select(run_manifest.c.frozen_config).where(
                    run_manifest.c.tenant_id == actor.tenant_id,
                    run_manifest.c.run_id == run_id,
                )
            ).scalar_one_or_none()
            report_v2_run = (manifest or {}).get("agent_contract_generation") == "v6"
            report_v3_run = (manifest or {}).get("architecture_generation") == "supervisor-1p4-report-v3"
            self._validate_synthesis_contract(document, report_v2_run=report_v2_run)
            if run["status"] != "RUNNING" or run["current_stage"] != "SUPERVISOR_SYNTHESIS":
                raise CompletionValidationError("report commit requires SUPERVISOR_SYNTHESIS")
            synthesis_task = (
                session.execute(
                    select(task).where(
                        task.c.tenant_id == actor.tenant_id,
                        task.c.id == synthesis_task_id,
                        task.c.run_id == run_id,
                        task.c.agent_identity_ref.in_(
                            ("evaluation-manager@4.0", "evaluation-manager@5.0", "evaluation-manager@6.0")
                        ),
                        task.c.stage_code == "SUPERVISOR_SYNTHESIS",
                    )
                )
                .mappings()
                .first()
            )
            if synthesis_task is None or synthesis_task["status"] not in {"PENDING", "RUNNING"}:
                raise CompletionValidationError("synthesis must come from the assigned generation-v4 manager task")
            decision_id = UUID(str(document["deterministic_decision_ref"]))
            decision_row = (
                session.execute(
                    select(decision).where(
                        decision.c.tenant_id == actor.tenant_id,
                        decision.c.id == decision_id,
                        decision.c.run_id == run_id,
                    )
                )
                .mappings()
                .first()
            )
            if decision_row is None:
                raise CompletionValidationError("synthesis references a nonexistent deterministic Decision")
            if report_v2_run:
                return self._commit_report_v2(
                    session,
                    actor.tenant_id,
                    dict(run),
                    dict(synthesis_task),
                    dict(decision_row),
                    document,
                    now,
                    report_version="3.0" if report_v3_run else "2.0",
                )
            if (
                UUID(str(document["run_id"])) != run_id
                or document["deterministic_recommendation"] != decision_row["recommendation"]
            ):
                raise CompletionValidationError("the manager cannot alter the deterministic Decision")
            audited = self._audited_findings(session, actor.tenant_id, run_id)
            evidence_rows = (
                session.execute(
                    select(evidence.c.id, evidence.c.object_key).where(
                        evidence.c.tenant_id == actor.tenant_id,
                        evidence.c.run_id == run_id,
                    )
                )
                .mappings()
                .all()
            )
            _apply_canonical_synthesis_evidence_citations(
                document,
                {str(row["id"]): str(row["object_key"]) for row in evidence_rows},
            )
            self._validate_citations(document, audited)
            expected_changes = self._version_changes(session, actor.tenant_id, run, audited)
            if document["version_changes"] != expected_changes:
                raise CompletionValidationError("version_changes must come from the current audited history comparison")
            conflict = document["proposed_recommendation"] != decision_row["recommendation"]
            if bool(document["decision_conflict"]) != conflict:
                raise CompletionValidationError("decision_conflict must record, not conceal, a manager disagreement")
            synthesis_sha = hashlib.sha256(_canonical(document)).hexdigest()
            session.execute(
                manager_synthesis.insert().values(
                    id=UUID(str(document["synthesis_id"])),
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    task_id=synthesis_task_id,
                    dispatch_epoch=int(synthesis_task["dispatch_epoch"]),
                    deterministic_candidate=decision_row["recommendation"],
                    proposed_recommendation=document["proposed_recommendation"],
                    raw_synthesis=document,
                    synthesis_sha256=synthesis_sha,
                    status="DECISION_CONFLICT" if conflict else "ACCEPTED",
                    approval_request_id=None,
                    created_at=now,
                )
            )
            report_document = self._render_report(run_id, dict(decision_row), document, audited, expected_changes)
            report_body = _canonical(report_document)
            report_sha = hashlib.sha256(report_body).hexdigest()
            report_key = f"tenant/{actor.tenant_id}/run/{run_id}/reports/{report_sha}.json"
            if self._objects.put_private(report_key, report_body, "application/json") != report_sha:
                raise CompletionValidationError("object store changed the rendered report digest")
            report_id = uuid4()
            session.execute(
                report.insert().values(
                    id=report_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    decision_id=decision_id,
                    object_key=report_key,
                    sha256=report_sha,
                    status="COMMITTED",
                    action_items=document["actions"],
                    supersedes_id=None,
                    created_at=now,
                )
            )
            dossier_document = {
                "schema_version": "1.0",
                "project_id": str(run["project_id"]),
                "product_version_id": str(run["product_version_id"]),
                "run_id": str(run_id),
                "decision_id": str(decision_id),
                "report_id": str(report_id),
                "recommendation": decision_row["recommendation"],
                "score": decision_row["dimension_grades"],
                "version_changes": expected_changes,
            }
            dossier_sha = hashlib.sha256(_canonical(dossier_document)).hexdigest()
            dossier_id = uuid4()
            session.execute(
                project_dossier_snapshot.insert().values(
                    id=dossier_id,
                    tenant_id=actor.tenant_id,
                    project_id=run["project_id"],
                    product_version_id=run["product_version_id"],
                    run_id=run_id,
                    decision_id=decision_id,
                    report_id=report_id,
                    schema_version="1.0",
                    document=dossier_document,
                    sha256=dossier_sha,
                    created_at=now,
                )
            )
            self._assert_completion_checklist(session, actor.tenant_id, run_id, synthesis_task_id, run)
            session.execute(
                update(task)
                .where(task.c.tenant_id == actor.tenant_id, task.c.id == synthesis_task_id)
                .values(status="SUCCEEDED", updated_at=now)
            )
            session.execute(
                update(stage)
                .where(
                    stage.c.tenant_id == actor.tenant_id,
                    stage.c.run_id == run_id,
                    stage.c.code == "SUPERVISOR_SYNTHESIS",
                )
                .values(status="COMPLETED", completed_at=now)
            )
            session.execute(
                run_status_history.insert().values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    from_status="RUNNING",
                    to_status="COMPLETED",
                    reason="Decision, Report, and Project Dossier committed atomically",
                    failure_class=None,
                    occurred_at=now,
                )
            )
            session.execute(
                update(evaluation_run)
                .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                .values(status="COMPLETED", current_stage="COMPLETED", updated_at=now)
            )
            return CompletionResult(decision_id, report_id, dossier_id, decision_row["recommendation"], conflict)

    def _commit_report_v2(
        self,
        session: Session,
        tenant_id: UUID,
        run: dict[str, Any],
        synthesis_task: Mapping[str, Any],
        decision_row: Mapping[str, Any],
        document: dict[str, Any],
        now: datetime,
        *,
        report_version: str = "2.0",
    ) -> CompletionResult:
        run_id = UUID(str(run["id"]))
        if UUID(str(document["run_id"])) != run_id:
            raise CompletionValidationError("ManagerSynthesisV2 targets a different Run")
        audited = self._audited_findings(session, tenant_id, run_id)
        citation_bases, source_directory, allowed_evidence_ids = self._report_citation_catalog(
            session, tenant_id, run_id, audited
        )
        report_synthesis, citations = _bind_report_citations(document, citation_bases, audited)
        product_row = (
            session.execute(
                select(project.c.name, product_version.c.stage)
                .join(
                    product_version,
                    (product_version.c.tenant_id == project.c.tenant_id)
                    & (product_version.c.project_id == project.c.id),
                )
                .where(
                    project.c.tenant_id == tenant_id,
                    project.c.id == run["project_id"],
                    product_version.c.id == run["product_version_id"],
                )
            )
            .mappings()
            .one()
        )
        report_id = uuid4()
        synthesis_sha = hashlib.sha256(_canonical(document)).hexdigest()
        agent_cards = self._agent_report_cards(session, tenant_id, run_id, audited)
        try:
            report_run = {
                **run,
                "product_title": product_row["name"],
                "stage": product_row["stage"],
                "locale": (run.get("state_flags") or {}).get("locale") or "zh-CN",
            }
            if report_version == "3.0":
                synthesis_v3 = {
                    **report_synthesis,
                    "schema_version": "3.0",
                    "locale": report_run["locale"],
                }
                report_document = SupervisorReportV3Builder().build(
                    report_id=report_id,
                    run=report_run,
                    decision=dict(decision_row),
                    synthesis=synthesis_v3,
                    driver_claims=self._dimension_driver_claims(synthesis_v3),
                    citations=citations,
                    source_directory=source_directory,
                    agent_report_cards=agent_cards,
                    allowed_evidence_ids=allowed_evidence_ids,
                    source_sha256=synthesis_sha,
                    audit_detail_ref=f"audit:run:{run_id}",
                )
            else:
                report_document = SupervisorReportV2Builder().build(
                    report_id=report_id,
                    run=report_run,
                    decision=dict(decision_row),
                    synthesis=report_synthesis,
                    citations=citations,
                    source_directory=source_directory,
                    agent_report_cards=agent_cards,
                    allowed_evidence_ids=allowed_evidence_ids,
                    source_sha256=synthesis_sha,
                    audit_detail_ref=f"audit:run:{run_id}",
                )
        except (SupervisorReportV2Error, SupervisorReportV3Error) as exc:
            raise CompletionValidationError(str(exc)) from exc
        report_body = _canonical(report_document)
        report_sha = hashlib.sha256(report_body).hexdigest()
        version_path = "v3" if report_version == "3.0" else "v2"
        report_key = f"tenant/{tenant_id}/run/{run_id}/reports/{version_path}/{report_sha}.json"
        stored_sha = self._objects.put_private(report_key, report_body, "application/json")
        metadata = self._objects.head(report_key)
        if stored_sha != report_sha or metadata is None or metadata.sha256 != report_sha:
            raise CompletionValidationError("object store changed the immutable supervisor report digest")

        conflict = bool(document["decision_conflict"])
        session.execute(
            manager_synthesis.insert().values(
                id=UUID(str(document["synthesis_id"])),
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=synthesis_task["id"],
                dispatch_epoch=int(synthesis_task["dispatch_epoch"]),
                deterministic_candidate=decision_row["recommendation"],
                proposed_recommendation=decision_row["recommendation"],
                raw_synthesis=document,
                synthesis_sha256=synthesis_sha,
                status="DECISION_CONFLICT" if conflict else "ACCEPTED",
                approval_request_id=None,
                created_at=now,
            )
        )
        session.execute(
            report.insert().values(
                id=report_id,
                tenant_id=tenant_id,
                run_id=run_id,
                decision_id=decision_row["id"],
                object_key=report_key,
                sha256=report_sha,
                status="COMMITTED",
                action_items=document["actions"],
                supersedes_id=None,
                created_at=now,
            )
        )
        for citation in citations:
            session.execute(
                report_claim_citation.insert().values(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    report_id=report_id,
                    claim_id=citation["claim_id"],
                    citation_id=citation["citation_id"],
                    evidence_id=UUID(str(citation["evidence_id"])),
                    source_locator_id=(
                        UUID(str(citation["source_locator_id"])) if citation["source_locator_id"] is not None else None
                    ),
                    support_role=citation["support_role"],
                    audit_status=citation["audit_status"],
                    label=citation["label"],
                    created_at=now,
                )
            )
        dossier_document = {
            "schema_version": report_version,
            "project_id": str(run["project_id"]),
            "product_version_id": str(run["product_version_id"]),
            "product_title": product_row["name"],
            "run_id": str(run_id),
            "decision_id": str(decision_row["id"]),
            "report_id": str(report_id),
            "recommendation": decision_row["recommendation"],
            "score": decision_row["dimension_grades"],
            "report_sha256": report_sha,
        }
        dossier_sha = hashlib.sha256(_canonical(dossier_document)).hexdigest()
        dossier_id = uuid4()
        session.execute(
            project_dossier_snapshot.insert().values(
                id=dossier_id,
                tenant_id=tenant_id,
                project_id=run["project_id"],
                product_version_id=run["product_version_id"],
                run_id=run_id,
                decision_id=decision_row["id"],
                report_id=report_id,
                schema_version=report_version,
                document=dossier_document,
                sha256=dossier_sha,
                created_at=now,
            )
        )
        self._assert_completion_checklist(session, tenant_id, run_id, synthesis_task["id"], run)
        session.execute(
            update(task)
            .where(task.c.tenant_id == tenant_id, task.c.id == synthesis_task["id"])
            .values(status="SUCCEEDED", updated_at=now)
        )
        session.execute(
            update(stage)
            .where(stage.c.tenant_id == tenant_id, stage.c.run_id == run_id, stage.c.code == "SUPERVISOR_SYNTHESIS")
            .values(status="COMPLETED", completed_at=now)
        )
        session.execute(
            run_status_history.insert().values(
                id=uuid4(),
                tenant_id=tenant_id,
                run_id=run_id,
                from_status="RUNNING",
                to_status="COMPLETED",
                reason=f"immutable SupervisorReportDocumentV{report_version[0]} committed with Decision and Dossier",
                failure_class=None,
                occurred_at=now,
            )
        )
        session.execute(
            update(evaluation_run)
            .where(evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id)
            .values(status="COMPLETED", current_stage="COMPLETED", updated_at=now)
        )
        return CompletionResult(
            UUID(str(decision_row["id"])),
            report_id,
            dossier_id,
            str(decision_row["recommendation"]),
            conflict,
        )

    @staticmethod
    def _dimension_driver_claims(synthesis: dict[str, Any]) -> list[dict[str, Any]]:
        section_by_dimension = {
            "user_value": {"USER"},
            "product_capability": {"PRODUCT"},
            "investment_potential": {"INVESTMENT"},
            "evidence_quality": {"CROSS_DOMAIN", "INFORMATION_GAP"},
        }
        claims = list(synthesis["claims"])
        locale = str(synthesis["locale"])
        drivers: list[dict[str, Any]] = []
        for dimension, sections in section_by_dimension.items():
            candidates = [item for item in claims if item["section"] in sections][:3]
            if not candidates:
                text = (
                    f"{dimension} 缺少可核验的加减分依据，需要复验。"
                    if locale == "zh-CN"
                    else f"{dimension} lacks auditable score drivers and requires validation."
                )
                candidates = [
                    {
                        "claim_id": f"claim-dimension-{dimension}-pending",
                        "section": "INFORMATION_GAP",
                        "text": text,
                        "status": "PENDING_VALIDATION",
                        "decision_relevance": "IMPORTANT",
                        "citation_ids": [],
                        "score_bearing": False,
                    }
                ]
            for claim in candidates:
                if claim["status"] in {"PENDING_VALIDATION", "CONFLICTED"}:
                    polarity = "PENDING"
                elif claim["section"] == "CRITICAL_ISSUE" or claim["status"] == "DOWNGRADED":
                    polarity = "NEGATIVE"
                else:
                    polarity = "POSITIVE"
                drivers.append({"dimension": dimension, "polarity": polarity, "claim": claim})
        return drivers

    @staticmethod
    def _locked_run(session: Session, tenant_id: UUID, run_id: UUID) -> dict[str, Any]:
        row = (
            session.execute(
                select(evaluation_run)
                .where(evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFoundError("run was not found")
        return dict(row)

    @staticmethod
    def _planned_tasks(session: Session, tenant_id: UUID, run_id: UUID, plan_id: UUID) -> list[dict[str, Any]]:
        rows = (
            session.execute(
                select(task.c.status, task.c.required, agent_task_ticket.c.target_agent)
                .join(
                    agent_task_ticket,
                    (agent_task_ticket.c.tenant_id == task.c.tenant_id) & (agent_task_ticket.c.task_id == task.c.id),
                )
                .where(
                    task.c.tenant_id == tenant_id,
                    task.c.run_id == run_id,
                    task.c.dispatch_epoch == 0,
                    agent_task_ticket.c.plan_id == plan_id,
                )
            )
            .mappings()
            .all()
        )
        return [{"agent": row["target_agent"], "status": row["status"], "required": row["required"]} for row in rows]

    @staticmethod
    def _audited_findings(session: Session, tenant_id: UUID, run_id: UUID) -> list[dict[str, Any]]:
        rows = (
            session.execute(
                select(
                    finding,
                    evidence_audit.c.decision.label("audit_decision"),
                    evidence_audit.c.audit_round,
                    evidence_audit.c.score_components,
                    evidence_audit.c.referenced_evidence_ids,
                )
                .join(
                    evidence_audit,
                    (evidence_audit.c.tenant_id == finding.c.tenant_id) & (evidence_audit.c.finding_id == finding.c.id),
                )
                .where(finding.c.tenant_id == tenant_id, finding.c.run_id == run_id)
                .order_by(finding.c.id, evidence_audit.c.audit_round.desc())
            )
            .mappings()
            .all()
        )
        latest: dict[UUID, dict[str, Any]] = {}
        for row in rows:
            if row["id"] not in latest:
                latest[row["id"]] = {
                    "id": row["id"],
                    "finding": row["structured_result"]["finding"],
                    "report_ref": row["structured_result"]["report_ref"],
                    "audit_decision": row["audit_decision"],
                    "audit_round": row["audit_round"],
                    "referenced_evidence_ids": list(row["referenced_evidence_ids"] or []),
                    **dict(row["score_components"] or {}),
                }
        finding_count = session.execute(
            select(func.count())
            .select_from(finding)
            .where(finding.c.tenant_id == tenant_id, finding.c.run_id == run_id)
        ).scalar_one()
        if not latest or len(latest) != finding_count:
            raise CompletionValidationError("every available Finding must have a completed audit")
        return list(latest.values())

    @staticmethod
    def _report_citation_catalog(
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        audited: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[UUID]]:
        evidence_rows = (
            session.execute(
                select(evidence.c.id, evidence.c.object_key).where(
                    evidence.c.tenant_id == tenant_id,
                    evidence.c.run_id == run_id,
                )
            )
            .mappings()
            .all()
        )
        allowed_evidence_ids = {row["id"] for row in evidence_rows}
        evidence_by_ref = {str(row["object_key"]): row["id"] for row in evidence_rows}
        locator_rows = (
            session.execute(
                select(evidence_source_locator).where(
                    evidence_source_locator.c.tenant_id == tenant_id,
                    evidence_source_locator.c.evidence_id.in_(allowed_evidence_ids),
                )
            )
            .mappings()
            .all()
            if allowed_evidence_ids
            else []
        )
        locators_by_evidence: dict[UUID, list[Mapping[str, Any]]] = {}
        source_directory: list[dict[str, Any]] = []
        for row in locator_rows:
            locators_by_evidence.setdefault(row["evidence_id"], []).append(dict(row))
            item = {
                "source_locator_id": str(row["id"]),
                "evidence_id": str(row["evidence_id"]),
                "source_kind": row["source_kind"],
                "title": row["title"],
                "publisher": row["publisher"],
                "published_at": row["published_at"].isoformat() if row["published_at"] else None,
                "fetched_at": row["fetched_at"].isoformat(),
                "locator": row["locator"],
                "region": row["region"],
                "independence_group": row["independence_group"],
                "content_sha256": row["content_sha256"],
            }
            if row["canonical_url"] is not None:
                item["canonical_url"] = row["canonical_url"]
            source_directory.append(item)
        citation_bases: list[dict[str, Any]] = []
        audit_status_by_citation = {
            "VERIFIED": "VERIFIED",
            "DOWNGRADED": "DOWNGRADED",
            "PENDING_VALIDATION": "NEEDS_MORE",
            "REJECTED": "REJECTED",
        }
        for item in audited:
            first_item_base = len(citation_bases)
            evidence_ids = {
                UUID(str(evidence_id))
                for evidence_id in item.get("referenced_evidence_ids", [])
                if UUID(str(evidence_id)) in allowed_evidence_ids
            }
            if not evidence_ids:
                evidence_ids = {
                    evidence_by_ref[reference]
                    for reference in item["finding"].get("evidence_refs", [])
                    if reference in evidence_by_ref
                }
            label = 0
            for evidence_id in sorted(evidence_ids, key=str):
                locators: list[Mapping[str, Any] | None] = [*locators_by_evidence.get(evidence_id, [])]
                if not locators:
                    locators.append(None)
                for locator in locators:
                    label += 1
                    citation_bases.append(
                        {
                            "citation_id": f"citation-{item['id'].hex}-{label}",
                            "source_claim_id": f"claim-{item['id']}",
                            "evidence_id": str(evidence_id),
                            "source_locator_id": str(locator["id"]) if locator is not None else None,
                            "support_role": "SUPPORT",
                            "audit_status": audit_status_by_citation.get(str(item.get("citation_status")), "REJECTED"),
                            "label": label,
                        }
                    )
            item_bases = citation_bases[first_item_base:]
            report_evidence_id = evidence_by_ref.get(str(item["report_ref"].get("ref", "")))
            if report_evidence_id is not None and item_bases:
                rank = {"VERIFIED": 0, "DOWNGRADED": 1, "NEEDS_MORE": 2, "REJECTED": 3}
                preferred = min(
                    item_bases,
                    key=lambda base: (
                        rank.get(str(base["audit_status"]), 4),
                        base["source_locator_id"] is None,
                        str(base["citation_id"]),
                    ),
                )
                preferred.setdefault("aliases", []).append(f"citation-{report_evidence_id}")
        if citation_bases:
            rank = {"VERIFIED": 0, "DOWNGRADED": 1, "NEEDS_MORE": 2, "REJECTED": 3}
            preferred = min(
                citation_bases,
                key=lambda base: (
                    rank.get(str(base["audit_status"]), 4),
                    base["source_locator_id"] is None,
                    str(base["citation_id"]),
                ),
            )
            for row in evidence_rows:
                if "/agent-reports/evidence-auditor/" in str(row["object_key"]):
                    preferred.setdefault("aliases", []).append(f"citation-{row['id']}")
        return citation_bases, source_directory, allowed_evidence_ids

    @staticmethod
    def _agent_report_cards(
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        audited: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        codes = ("user-evidence", "product-engineering", "business-investment", "evidence-auditor")
        rows = (
            session.execute(
                select(agent_report_artifact)
                .where(
                    agent_report_artifact.c.tenant_id == tenant_id,
                    agent_report_artifact.c.run_id == run_id,
                    agent_report_artifact.c.agent_code.in_(codes),
                    agent_report_artifact.c.status == "AVAILABLE",
                )
                .order_by(
                    agent_report_artifact.c.agent_code,
                    agent_report_artifact.c.revision.desc(),
                    agent_report_artifact.c.created_at.desc(),
                )
            )
            .mappings()
            .all()
        )
        latest: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            latest.setdefault(str(row["agent_code"]), dict(row))
        if set(latest) != set(codes):
            raise CompletionValidationError("supervisor report requires all four immutable Agent report artifacts")
        titles = {
            "user-evidence": "用户报告",
            "product-engineering": "产品经理报告",
            "business-investment": "投资人报告",
            "evidence-auditor": "证据审核报告",
        }
        claims_by_agent = {
            code: [
                f"claim-{item['id']}"
                for item in audited
                if item["finding"]["agent_code"] == code or code == "evidence-auditor"
            ][:5]
            for code in codes
        }
        return [
            {
                "agent_code": code,
                "report_id": str(latest[code]["id"]),
                "title": titles[code],
                "summary_claim_ids": claims_by_agent[code],
                "source_sha256": latest[code]["sha256"],
            }
            for code in codes
        ]

    @staticmethod
    def _comparison_findings(audited: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "claim_id": f"claim-{item['id']}",
                "risk": item["audit_decision"] in {"REJECTED", "NEEDS_MORE"} or item["finding"].get("grade") == "WEAK",
                "audit_decision": item["audit_decision"],
                "citation_status": item.get("citation_status"),
                "support_strength": item.get("support_strength", "NONE"),
            }
            for item in audited
        ]

    def _build_comparison_snapshot(
        self,
        session: Session,
        tenant_id: UUID,
        run: Mapping[str, Any],
        audited: list[dict[str, Any]],
        score: DeterministicScore,
        profile: dict[str, Any],
        profile_ref: str,
    ) -> dict[str, Any]:
        compatibility = profile["comparison_compatibility"]
        current = {
            "run_id": str(run["id"]),
            "input_snapshot_sha256": str(run["input_snapshot_sha256"]),
            "content_fingerprint_sha256": str(run["content_fingerprint_sha256"]),
            "standard_version": str(run["standard_version"]),
            "score_profile_ref": profile_ref,
            "score_profile_sha256": hashlib.sha256(self._profile_path(profile_ref).read_bytes()).hexdigest(),
            "report_profile_ref": str(run["report_profile_ref"]),
            "required_dimension_set": str(compatibility["required_dimension_set"]),
            "potential_index": score.score,
            "dimension_scores": score.dimension_scores,
            "findings": self._comparison_findings(audited),
        }
        baseline_id = run.get("baseline_run_id")
        if baseline_id is None:
            return build_report_comparison(current, None)
        prior_run = (
            session.execute(
                select(evaluation_run).where(
                    evaluation_run.c.tenant_id == tenant_id,
                    evaluation_run.c.id == baseline_id,
                )
            )
            .mappings()
            .one()
        )
        prior_plan = (
            session.execute(
                select(agent_plan.c.raw_plan).where(
                    agent_plan.c.tenant_id == tenant_id,
                    agent_plan.c.run_id == baseline_id,
                    agent_plan.c.status == "ACCEPTED",
                )
            )
            .mappings()
            .one()
        )
        prior_decision = (
            session.execute(
                select(decision)
                .where(decision.c.tenant_id == tenant_id, decision.c.run_id == baseline_id)
                .order_by(decision.c.created_at.desc(), decision.c.id.desc())
            )
            .mappings()
            .first()
        )
        prior_report_id = session.execute(
            select(report.c.id)
            .where(
                report.c.tenant_id == tenant_id,
                report.c.run_id == baseline_id,
                report.c.status == "COMMITTED",
            )
            .order_by(report.c.created_at.desc(), report.c.id.desc())
            .limit(1)
        ).scalar_one()
        if prior_decision is None:
            raise CompletionValidationError("bound comparison baseline has no committed Decision")
        prior_profile_ref = str(prior_plan["raw_plan"]["score_profile_ref"])
        prior_profile = self._load_profile(prior_profile_ref)
        prior_grades = dict(prior_decision["dimension_grades"] or {})
        prior = {
            "run_id": str(baseline_id),
            "report_id": str(prior_report_id),
            "input_snapshot_sha256": str(
                prior_run["input_snapshot_sha256"] or hashlib.sha256(f"legacy-input:{baseline_id}".encode()).hexdigest()
            ),
            "content_fingerprint_sha256": str(
                prior_run["content_fingerprint_sha256"]
                or hashlib.sha256(f"legacy-content:{baseline_id}".encode()).hexdigest()
            ),
            "standard_version": str(prior_run["standard_version"]),
            "score_profile_ref": prior_profile_ref,
            "score_profile_sha256": hashlib.sha256(self._profile_path(prior_profile_ref).read_bytes()).hexdigest(),
            "report_profile_ref": str(prior_run["report_profile_ref"] or "supervisor-report@1.0"),
            "required_dimension_set": str(
                prior_profile.get("comparison_compatibility", {}).get(
                    "required_dimension_set", f"legacy-{prior_profile['profile_id']}"
                )
            ),
            "potential_index": float(prior_grades.get("score", 0)),
            "dimension_scores": dict(prior_grades.get("dimension_scores") or {}),
            "findings": self._comparison_findings(self._audited_findings(session, tenant_id, baseline_id)),
        }
        return build_report_comparison(current, prior)

    @staticmethod
    def _version_changes(
        session: Session, tenant_id: UUID, run: dict[str, Any], current: list[dict[str, Any]]
    ) -> dict[str, list[str]]:
        baseline_id = run.get("baseline_run_id")
        if baseline_id is None:
            return {"improved": [], "unchanged": [], "new_risks": []}
        prior = SupervisorCompletionApplication._audited_findings(session, tenant_id, baseline_id)

        return VersionChangeComparator().compare(prior, current)

    def _materialize_synthesis_task(
        self,
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        plan: Mapping[str, Any],
        context_key: str,
        context_sha: str,
        report_preferences: Mapping[str, Any],
        agent_version: str,
        report_v2_run: bool,
        now: datetime,
    ) -> UUID:
        manager_skill_version_id = session.execute(
            select(skill_version.c.id).where(
                skill_version.c.skill_code == "launchscope-evaluation-manager-handoff-v1",
                skill_version.c.version == "1.0",
            )
        ).scalar_one_or_none()
        if manager_skill_version_id is None:
            raise CompletionValidationError(
                "registered Skill version is missing: launchscope-evaluation-manager-handoff-v1@1.0"
            )
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
                code="SUPERVISOR_SYNTHESIS",
                ordinal=ordinal,
                status="READY",
                started_at=None,
                completed_at=None,
            )
        )
        task_id, ticket_id = uuid4(), uuid4()
        deadline = now + timedelta(seconds=3600)
        input_ref = f"object:{context_key}#sha256={context_sha}"
        locale = "en" if report_preferences.get("locale") == "en" else "zh-CN"
        language_requirement = (
            "Write every user-facing summary, risk, opportunity, action, and evidence gap "
            "in clear English for a student."
            if locale == "en"
            else (
                "所有面向用户的摘要、风险、机会、行动和证据缺口必须使用简体中文，"
                "面向学生说人话，不得直接输出内部枚举或英文长段落。"
            )
        )
        public_summary = {
            "schema_version": "3.0",
            "ticket_id": str(ticket_id),
            "run_id": str(run_id),
            "task_id": str(task_id),
            "plan_id": str(plan["id"]),
            "plan_version": int(plan["plan_version"]),
            "dispatch_epoch": 0,
            "target_agent": "evaluation-manager",
            "objective": (
                f"synthesize audited findings without changing the deterministic decision. {language_requirement}"
            ),
            "input_refs": [input_ref],
            "analysis_dimensions": ["audited-cross-domain-synthesis"],
            "region_scope": ["as-audited"],
            "as_of": now.date().isoformat(),
            "tool_policy": [
                "launchscope-context.get.v2" if agent_version in {"5.0", "6.0"} else "launchscope-context.get.v1"
            ],
            "success_conditions": [
                (
                    "valid ManagerSynthesisV2 using only citation_ids from the immutable context"
                    if report_v2_run
                    else "valid ManagerSynthesisV1 with only existing Finding and Evidence citations"
                ),
                language_requirement,
            ],
            "required": True,
            "deadline_at": deadline.isoformat().replace("+00:00", "Z"),
            "report_contract": "manager-synthesis.v2" if report_v2_run else "domain-report-ref.v1",
            "handoff_contract": "manager-synthesis.v2" if report_v2_run else "agent-handoff.v3",
        }
        digest = hashlib.sha256(_canonical(public_summary)).hexdigest()
        session.execute(
            task.insert().values(
                id=task_id,
                tenant_id=tenant_id,
                run_id=run_id,
                stage_id=stage_id,
                agent_identity_id=None,
                skill_version_id=manager_skill_version_id,
                stage_code="SUPERVISOR_SYNTHESIS",
                agent_identity_ref=f"evaluation-manager@{agent_version}",
                skill_ref="launchscope-evaluation-manager-handoff-v1",
                skill_version="1.0",
                status="READY",
                lease_token=None,
                idempotency_key=f"v4-synthesis:{run_id}",
                dependencies=[],
                tool_allowlist=public_summary["tool_policy"],
                budget_slice={"suggested_usd": 0},
                timeout_seconds=3600,
                success_condition=public_summary["success_conditions"],
                evidence_requirement="all critical judgments cite existing Finding or Evidence refs",
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
            agent_task_ticket.insert().values(
                id=ticket_id,
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=task_id,
                plan_id=plan["id"],
                dispatch_epoch=0,
                target_agent="evaluation-manager",
                ticket_sha256=digest,
                public_summary=public_summary,
                usage_baseline=None,
                status="PREPARED",
                expires_at=deadline,
                created_at=now,
                delivered_at=None,
            )
        )
        if enqueue_ready_tasks(session, tenant_id, run_id, "SUPERVISOR_SYNTHESIS") != 1:
            raise CompletionValidationError("generation-v4 synthesis must enqueue exactly one manager task")
        return task_id

    @staticmethod
    def _validate_citations(document: dict[str, Any], audited: list[dict[str, Any]]) -> None:
        finding_ids = {str(item["id"]) for item in audited}
        evidence_refs = {ref for item in audited for ref in item["finding"]["evidence_refs"]}
        cited_findings: set[str] = set()
        for citation in document["citations"]:
            reference = str(citation["ref"])
            if citation["kind"] == "FINDING":
                normalized = reference.removeprefix("finding:")
                if normalized not in finding_ids:
                    raise CompletionValidationError("manager synthesis cites a nonexistent Finding")
                cited_findings.add(normalized)
            elif reference.removeprefix("evidence:") not in evidence_refs and reference not in evidence_refs:
                raise CompletionValidationError("manager synthesis cites a nonexistent Evidence ref")
        required_findings = {
            str(item["id"]) for item in audited if item["audit_decision"] in {"ACCEPTED", "DOWNGRADED"}
        }
        if not required_findings.issubset(cited_findings):
            raise CompletionValidationError("every positive critical Finding must be cited by the synthesis")

    @staticmethod
    def _render_report(
        run_id: UUID,
        decision_row: Mapping[str, Any],
        synthesis: dict[str, Any],
        audited: list[dict[str, Any]],
        version_changes: dict[str, list[str]],
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "run_id": str(run_id),
            "authoritative_recommendation": decision_row["recommendation"],
            "deterministic_score": decision_row["dimension_grades"],
            "manager_proposed_recommendation": synthesis["proposed_recommendation"],
            "decision_conflict": synthesis["decision_conflict"],
            "summary": synthesis["summary"],
            "cross_domain_analysis": synthesis["cross_domain_analysis"],
            "risks": synthesis["risks"],
            "conflicts": synthesis["conflicts"],
            "actions": synthesis["actions"],
            "version_changes": version_changes,
            "citations": synthesis["citations"],
            "audited_findings": [
                {
                    "finding_id": str(item["id"]),
                    "finding": item["finding"],
                    "report_ref": item["report_ref"],
                    "audit_decision": item["audit_decision"],
                    "audit_round": item["audit_round"],
                }
                for item in audited
            ],
        }

    @staticmethod
    def _assert_completion_checklist(
        session: Session, tenant_id: UUID, run_id: UUID, synthesis_task_id: UUID, run: dict[str, Any]
    ) -> None:
        rows = (
            session.execute(
                select(task.c.id, task.c.status, task.c.required, task.c.last_failure_class).where(
                    task.c.tenant_id == tenant_id,
                    task.c.run_id == run_id,
                    task.c.id != synthesis_task_id,
                    or_(
                        task.c.agent_identity_ref.like("%@4.0"),
                        task.c.agent_identity_ref.like("%@5.0"),
                        task.c.agent_identity_ref.like("%@6.0"),
                    ),
                )
            )
            .mappings()
            .all()
        )
        if any(row["status"] not in _FINAL_TASK_STATUSES for row in rows):
            raise CompletionValidationError("all planned tasks must be in legal terminal states")
        if any(row["required"] and row["status"] != "SUCCEEDED" for row in rows):
            raise CompletionValidationError("required tasks must succeed before completion")
        if any(
            row["last_failure_class"] in {"SUBMISSION_UNKNOWN", "BILLING_UNKNOWN", "PAID_CALL_TIMEOUT"} for row in rows
        ):
            raise CompletionValidationError("unknown submission or cost prohibits completion")
        flags = run.get("state_flags") or {}
        if any(flags.get(key) for key in ("waiting_for_user", "waiting_for_approval", "unknown_usage", "unknown_cost")):
            raise CompletionValidationError("waiting or unknown state prohibits completion")

    @staticmethod
    def _profile_path(reference: str) -> Path:
        prefix, version = reference.rsplit("@", 1)
        profile_id = prefix.removeprefix("score-profile:")
        if profile_id not in {
            "full-potential",
            "investment-review",
            "launch-review",
            "user-validation",
        }:
            raise CompletionValidationError("unknown score profile reference")
        if version == "2.0" and profile_id == "full-potential":
            return _ROOT / "packages/contracts/score/profiles/full-potential.v2.json"
        if version == "1.0":
            return _ROOT / f"packages/contracts/score/profiles/{profile_id}.v1.json"
        raise CompletionValidationError("unknown score profile reference")

    def _load_profile(self, reference: str) -> dict[str, Any]:
        return json.loads(self._profile_path(reference).read_text(encoding="utf-8"))

    def _validate_synthesis_contract(self, document: dict[str, Any], *, report_v2_run: bool) -> None:
        schema = self._synthesis_v2_schema if report_v2_run else self._synthesis_schema
        label = "ManagerSynthesisV2" if report_v2_run else "ManagerSynthesisV1"
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
            key=lambda item: item.json_path,
        )
        if errors:
            raise CompletionValidationError(f"{label} contract violation at {errors[0].json_path}")

    @staticmethod
    def _require_enabled() -> None:
        if not supervisor_1p4_enabled():
            raise IntakeValidationError("supervisor 1+4 generation is disabled")


__all__ = [
    "CompletionResult",
    "CompletionValidationError",
    "DeterministicScore",
    "DeterministicScoringEngine",
    "ScoringPreparation",
    "SupervisorCompletionApplication",
    "VersionChangeComparator",
]
