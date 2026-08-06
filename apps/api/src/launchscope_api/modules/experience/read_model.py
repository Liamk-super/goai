"""Durable PostgreSQL projections for the T10 experience surfaces.

This module deliberately does not mutate EvaluationRun, Evidence, Finding,
Decision or Report facts.  The UI receives projections derived from the same
tenant-scoped PostgreSQL transaction used by the application services.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    agentteams_run_binding,
    budget_reservation,
    decision,
    decision_finding,
    evaluation_run,
    evidence,
    evidence_audit,
    finding,
    finding_evidence,
    matrix_event_receipt,
    matrix_handoff,
    outbox_message,
    project,
    report,
    run_status_history,
    skill_invocation,
    stage,
    task,
    tool_invocation,
    workspace_member,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.modules.identity_tenant.application import Actor, AuthorizationError, NotFoundError
from launchscope_domain.value_objects import TenantScope


class CursorInvalidError(ValueError):
    """The SSE cursor is not a durable cursor for the requested tenant/run."""


def _cursor(row_id: UUID) -> str:
    return f"event.{row_id}"


def _parse_cursor(value: str) -> UUID:
    if not value.startswith("event."):
        raise CursorInvalidError("cursor is not a durable run-event cursor")
    try:
        return UUID(value.removeprefix("event."))
    except ValueError as exc:
        raise CursorInvalidError("cursor is malformed") from exc


@dataclass(frozen=True, slots=True)
class RunEvent:
    cursor: str
    event_type: str
    data: dict[str, object]


class ExperienceReadApplication:
    """Read model with explicit tenant membership and redaction boundaries."""

    def __init__(self, sessions: sessionmaker[Session], *, ops_sessions: sessionmaker[Session] | None = None) -> None:
        self._sessions = sessions
        self._ops_sessions = ops_sessions

    def list_projects(self, actor: Actor, *, limit: int = 50) -> list[dict[str, object]]:
        with self._session(actor) as session:
            statement = (
                select(project)
                .join(
                    workspace_member,
                    (workspace_member.c.tenant_id == project.c.tenant_id)
                    & (workspace_member.c.workspace_id == project.c.workspace_id),
                )
                .where(project.c.tenant_id == actor.tenant_id, workspace_member.c.actor_id == actor.actor_id)
                .order_by(project.c.updated_at.desc(), project.c.id.desc())
                .limit(limit)
            )
            return [
                {
                    "project_id": str(row["id"]),
                    "workspace_id": str(row["workspace_id"]),
                    "name": row["name"],
                    "status": row["dossier_status"],
                }
                for row in session.execute(statement).mappings()
            ]

    def get_run(self, actor: Actor, run_id: UUID) -> dict[str, object]:
        with self._session(actor) as session:
            row = self._run_row(session, actor, run_id)
            cursor = self._latest_cursor(session, actor.tenant_id, run_id)
            return self._run_projection(row, cursor)

    def list_runs(self, actor: Actor, project_id: UUID, *, limit: int = 50) -> list[dict[str, object]]:
        with self._session(actor) as session:
            statement = self._visible_runs(actor).where(evaluation_run.c.project_id == project_id).limit(limit)
            rows = session.execute(statement).mappings().all()
            return [self._run_projection(row, self._latest_cursor(session, actor.tenant_id, row["id"])) for row in rows]

    def run_events(
        self, actor: Actor, run_id: UUID, cursor: str | None
    ) -> tuple[dict[str, object], tuple[RunEvent, ...]]:
        with self._session(actor) as session:
            run = self._run_row(session, actor, run_id)
            snapshot = self._run_projection(run, self._latest_cursor(session, actor.tenant_id, run_id))
            if cursor is None:
                return snapshot, ()
            ordered = (
                session.execute(
                    select(
                        run_status_history.c.id,
                        run_status_history.c.to_status,
                        run_status_history.c.reason,
                        run_status_history.c.failure_class,
                        run_status_history.c.occurred_at,
                    )
                    .where(run_status_history.c.tenant_id == actor.tenant_id, run_status_history.c.run_id == run_id)
                    .order_by(run_status_history.c.occurred_at, run_status_history.c.id)
                )
                .mappings()
                .all()
            )
            if cursor == "event.initial":
                after_index = -1
            else:
                after_id = _parse_cursor(cursor)
                positions = {row["id"]: index for index, row in enumerate(ordered)}
                if after_id not in positions:
                    raise CursorInvalidError("cursor does not belong to this durable run event stream")
                after_index = positions[after_id]
            return snapshot, tuple(
                RunEvent(
                    cursor=_cursor(row["id"]),
                    event_type="run.status_changed",
                    data={
                        "run_id": str(run_id),
                        "status": row["to_status"],
                        "reason": row["reason"],
                        "failure_class": row["failure_class"],
                        "occurred_at": _iso(row["occurred_at"]),
                    },
                )
                for row in ordered[after_index + 1 :]
            )

    def report(self, actor: Actor, run_id: UUID) -> dict[str, object]:
        with self._session(actor) as session:
            run_row = self._run_row(session, actor, run_id)
            report_row = (
                session.execute(
                    select(report)
                    .where(report.c.tenant_id == actor.tenant_id, report.c.run_id == run_id)
                    .order_by(report.c.created_at.desc(), report.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if report_row is None:
                raise NotFoundError("no durable report has been committed for this run")
            decision_row = (
                session.execute(
                    select(decision).where(
                        decision.c.tenant_id == actor.tenant_id, decision.c.id == report_row["decision_id"]
                    )
                )
                .mappings()
                .one()
            )
            chain = self._evidence_chain(session, actor.tenant_id, decision_row["id"], report_row["id"])
            baseline_grades = session.execute(
                select(decision.c.dimension_grades)
                .join(
                    evaluation_run,
                    (evaluation_run.c.tenant_id == decision.c.tenant_id)
                    & (evaluation_run.c.id == decision.c.run_id),
                )
                .where(
                    decision.c.tenant_id == actor.tenant_id,
                    evaluation_run.c.project_id == run_row["project_id"],
                    evaluation_run.c.id != run_id,
                    evaluation_run.c.standard_version == run_row["standard_version"],
                    evaluation_run.c.created_at < run_row["created_at"],
                )
                .order_by(evaluation_run.c.created_at.desc(), decision.c.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            dimension_results = self._dimension_results(decision_row["dimension_grades"], baseline_grades, chain)
            action_links = self._action_links(report_row["action_items"], dimension_results)
            calibration_rows = session.execute(
                select(evidence_audit.c.finding_id, evidence_audit.c.decision, evidence_audit.c.reason)
                .where(evidence_audit.c.tenant_id == actor.tenant_id, evidence_audit.c.run_id == run_id)
                .order_by(evidence_audit.c.audited_at)
            ).mappings().all()
            return {
                "report_id": str(report_row["id"]),
                "run_id": str(run_id),
                "project_id": str(run_row["project_id"]),
                "decision_id": str(decision_row["id"]),
                "recommendation": decision_row["recommendation"],
                "standard_version": decision_row["standard_version"],
                "dimension_grades": decision_row["dimension_grades"],
                "blocking_reasons": decision_row["hard_blocks"],
                "action_items": report_row["action_items"],
                "action_links": action_links,
                "dimension_results": dimension_results,
                "key_contradictions": list(decision_row["hard_blocks"])[:5],
                "geo_trend": self._geo_trend(dimension_results),
                "information_gaps": [
                    dimension for dimension, result in dimension_results.items()
                    if result["grade"] == "INSUFFICIENT_EVIDENCE"
                ],
                "calibration_results": [
                    {"finding_id": str(item["finding_id"]), "decision": item["decision"], "reason": item["reason"]}
                    for item in calibration_rows
                    if item["decision"] != "APPROVED"
                ],
                "created_at": _iso(report_row["created_at"]),
                "evidence_chain": chain,
            }

    def report_by_id(self, actor: Actor, report_id: UUID) -> dict[str, object]:
        with self._session(actor) as session:
            row = (
                session.execute(
                    select(report.c.run_id).where(report.c.tenant_id == actor.tenant_id, report.c.id == report_id)
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFoundError("report was not found")
            # report() opens its own tenant transaction so that its permission
            # gate cannot be accidentally skipped by a future caller.
            run_id = row["run_id"]
        return self.report(actor, run_id)

    def agentteams_run(self, actor: Actor, run_id: UUID) -> dict[str, object]:
        """Body-free live topology and progress projection for the v0.2 Run page."""

        with self._session(actor) as session:
            self._run_row(session, actor, run_id)
            binding = session.execute(select(agentteams_run_binding).where(
                agentteams_run_binding.c.tenant_id == actor.tenant_id,
                agentteams_run_binding.c.run_id == run_id,
            )).mappings().first()
            tasks = session.execute(select(
                task.c.id, task.c.stage_code, task.c.agent_identity_ref, task.c.status,
                task.c.tool_allowlist, task.c.evidence_requirement, task.c.last_error,
                task.c.last_failure_class, task.c.side_effect_started, task.c.updated_at,
            ).where(task.c.tenant_id == actor.tenant_id, task.c.run_id == run_id).order_by(
                task.c.created_at, task.c.id
            )).mappings().all()
            stages = session.execute(select(
                stage.c.code, stage.c.ordinal, stage.c.status, stage.c.started_at, stage.c.completed_at,
            ).where(stage.c.tenant_id == actor.tenant_id, stage.c.run_id == run_id).order_by(
                stage.c.ordinal
            )).mappings().all()
            handoff_count = session.execute(select(matrix_handoff.c.id).where(
                matrix_handoff.c.tenant_id == actor.tenant_id, matrix_handoff.c.run_id == run_id,
            )).all().__len__()
            receipt_count = session.execute(select(matrix_event_receipt.c.id).where(
                matrix_event_receipt.c.tenant_id == actor.tenant_id, matrix_event_receipt.c.run_id == run_id,
            )).all().__len__()
            budget = session.execute(select(
                budget_reservation.c.limit_amount, budget_reservation.c.consumed_amount,
                budget_reservation.c.status,
            ).where(
                budget_reservation.c.tenant_id == actor.tenant_id,
                budget_reservation.c.run_id == run_id,
                budget_reservation.c.category == "run_total",
            )).mappings().first()
            return {
                "run_id": str(run_id),
                "team": {
                    "name": binding["team_name"] if binding else "launchscope-potential-review",
                    "agentteams_version": binding["agentteams_version"] if binding else "v1.2.0",
                    "binding_status": binding["binding_status"] if binding else "NOT_DISPATCHED",
                    "team_room_id": binding["team_room_id"] if binding else None,
                },
                "stages": [
                    {**dict(item), "started_at": _iso(item["started_at"]) if item["started_at"] else None,
                     "completed_at": _iso(item["completed_at"]) if item["completed_at"] else None}
                    for item in stages
                ],
                "tasks": [self._task_projection(session, actor.tenant_id, item) for item in tasks],
                "handoff_count": handoff_count, "matrix_event_count": receipt_count,
                "budget": {
                    "currency": "USD", "limit": str(budget["limit_amount"]),
                    "consumed": str(budget["consumed_amount"]), "status": budget["status"],
                } if budget else None,
            }

    @staticmethod
    def _task_projection(session: Session, tenant_id: UUID, item: Any) -> dict[str, object]:
        evidence_count = session.execute(
            select(func.count()).select_from(evidence).where(
                evidence.c.tenant_id == tenant_id, evidence.c.task_id == item["id"]
            )
        ).scalar_one()
        tools = session.execute(
            select(tool_invocation.c.tool_code, tool_invocation.c.status)
            .join(
                skill_invocation,
                (skill_invocation.c.tenant_id == tool_invocation.c.tenant_id)
                & (skill_invocation.c.id == tool_invocation.c.skill_invocation_id),
            )
            .where(skill_invocation.c.tenant_id == tenant_id, skill_invocation.c.task_id == item["id"])
            .order_by(tool_invocation.c.created_at)
        ).mappings().all()
        result = dict(item)
        result.update(
            id=str(item["id"]),
            summary=item["evidence_requirement"],
            evidence_count=evidence_count,
            failure_reason=item["last_error"],
            retryable=(
                item["last_failure_class"] in {"TRANSIENT", "VALIDATION"}
                and not item["side_effect_started"]
            ),
            needs_human_review=item["status"] in {"WAITING_FOR_USER", "WAITING_FOR_APPROVAL"},
            tool_invocations=[dict(tool) for tool in tools],
            updated_at=_iso(item["updated_at"]) if item["updated_at"] else None,
        )
        return result

    def evidence_object_key(self, actor: Actor, evidence_id: UUID) -> str:
        """Resolve an object key only after tenant membership and project visibility checks."""

        with self._session(actor) as session:
            row = session.execute(
                select(evidence.c.object_key)
                .join(
                    evaluation_run,
                    (evaluation_run.c.tenant_id == evidence.c.tenant_id)
                    & (evaluation_run.c.id == evidence.c.run_id),
                )
                .join(
                    project,
                    (project.c.tenant_id == evaluation_run.c.tenant_id)
                    & (project.c.id == evaluation_run.c.project_id),
                )
                .join(
                    workspace_member,
                    (workspace_member.c.tenant_id == project.c.tenant_id)
                    & (workspace_member.c.workspace_id == project.c.workspace_id),
                )
                .where(
                    evidence.c.tenant_id == actor.tenant_id,
                    evidence.c.id == evidence_id,
                    workspace_member.c.actor_id == actor.actor_id,
                )
            ).scalar_one_or_none()
            if row is None or row.startswith("deleted/"):
                raise NotFoundError("evidence was not found")
            return row

    def compare_runs(self, actor: Actor, project_id: UUID, run_id: UUID) -> dict[str, object]:
        with self._session(actor) as session:
            candidate = self._run_row(session, actor, run_id)
            if candidate["project_id"] != project_id:
                raise NotFoundError("run is outside the requested project")
            baseline = (
                session.execute(
                    self._visible_runs(actor)
                    .where(
                        evaluation_run.c.project_id == project_id,
                        evaluation_run.c.id != run_id,
                        evaluation_run.c.created_at < candidate["created_at"],
                    )
                    .order_by(evaluation_run.c.created_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if baseline is None:
                raise NotFoundError("a prior durable run is required for comparison")
            comparable = baseline["standard_version"] == candidate["standard_version"]
            grade_rows = session.execute(select(decision.c.run_id, decision.c.dimension_grades).where(
                decision.c.tenant_id == actor.tenant_id,
                decision.c.run_id.in_((baseline["id"], candidate["id"])),
            ).order_by(decision.c.created_at.desc())).all()
            by_run: dict[UUID, dict[str, str]] = {}
            for decision_run_id, grades in grade_rows:
                by_run.setdefault(decision_run_id, grades)
            rank = {"INSUFFICIENT_EVIDENCE": 0, "WEAK": 1, "MODERATE": 2, "STRONG": 3}
            changes: dict[str, str] = {}
            baseline_id = UUID(str(baseline["id"]))
            candidate_id = UUID(str(candidate["id"]))
            if comparable and baseline_id in by_run and candidate_id in by_run:
                dimensions = set(by_run[baseline_id]) | set(by_run[candidate_id])
                for dimension in dimensions:
                    before = rank.get(by_run[baseline_id].get(dimension, "INSUFFICIENT_EVIDENCE"), 0)
                    after = rank.get(by_run[candidate_id].get(dimension, "INSUFFICIENT_EVIDENCE"), 0)
                    changes[dimension] = (
                        "IMPROVED" if after > before else ("REGRESSED" if after < before else "UNCHANGED")
                    )
            return {
                "project_id": str(project_id),
                "baseline_run_id": str(baseline["id"]),
                "candidate_run_id": str(candidate["id"]),
                "comparable": comparable,
                "standard_version": baseline["standard_version"],
                "supplemental_standard_version": None if comparable else candidate["standard_version"],
                "baseline_status": baseline["status"],
                "candidate_status": candidate["status"],
                "dimension_changes": changes,
                "new_risks": [dimension for dimension, change in changes.items() if change == "REGRESSED"],
            }

    def ops_run(self, run_id: UUID) -> dict[str, object]:
        """Redacted cross-tenant operational projection; never returns material/report bodies."""
        with self._ops_session() as session:
            row = session.execute(select(evaluation_run).where(evaluation_run.c.id == run_id)).mappings().first()
            if row is None:
                raise NotFoundError("run was not found")
            return {
                "run_id": str(row["id"]),
                "tenant_id": str(row["tenant_id"]),
                "project_id": str(row["project_id"]),
                "status": row["status"],
                "current_stage": row["current_stage"],
                "standard_version": row["standard_version"],
                "attention_reason": row["attention_reason"],
                "updated_at": _iso(row["updated_at"]),
            }

    def ops_events(self, *, limit: int = 100) -> list[dict[str, object]]:
        """Only event metadata and payload hashes are visible to the Ops identity domain."""
        with self._ops_session() as session:
            rows = session.execute(
                select(outbox_message).order_by(outbox_message.c.occurred_at.desc()).limit(limit)
            ).mappings()
            return [
                {
                    "event_id": str(row["event_id"]),
                    "tenant_id": str(row["tenant_id"]),
                    "run_id": str(row["aggregate_id"]),
                    "event_type": row["event_type"],
                    "status": row["publish_status"],
                    "occurred_at": _iso(row["occurred_at"]),
                }
                for row in rows
            ]

    def _session(self, actor: Actor) -> AbstractContextManager[Session]:
        return tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id)

    def _ops_session(self) -> Session:
        if self._ops_sessions is None:
            raise AuthorizationError("Ops projection database role is not configured")
        return self._ops_sessions()

    @staticmethod
    def _run_projection(row: Any, cursor: str) -> dict[str, object]:
        return {
            "run_id": str(row["id"]),
            "project_id": str(row["project_id"]),
            "product_version_id": str(row["product_version_id"]),
            "status": row["status"],
            "standard_version": row["standard_version"],
            "current_cursor": cursor,
            "correlation_id": str(row["correlation_id"]),
            "current_stage": row["current_stage"],
            "attention_reason": row["attention_reason"],
            "updated_at": _iso(cast(datetime, row["updated_at"])),
        }

    def _visible_runs(self, actor: Actor) -> Select[Any]:
        return (
            select(evaluation_run)
            .join(
                project,
                (project.c.tenant_id == evaluation_run.c.tenant_id) & (project.c.id == evaluation_run.c.project_id),
            )
            .join(
                workspace_member,
                (workspace_member.c.tenant_id == project.c.tenant_id)
                & (workspace_member.c.workspace_id == project.c.workspace_id),
            )
            .where(evaluation_run.c.tenant_id == actor.tenant_id, workspace_member.c.actor_id == actor.actor_id)
            .order_by(evaluation_run.c.updated_at.desc(), evaluation_run.c.id.desc())
        )

    def _run_row(self, session: Session, actor: Actor, run_id: UUID) -> dict[str, object]:
        row = session.execute(self._visible_runs(actor).where(evaluation_run.c.id == run_id)).mappings().first()
        if row is None:
            raise NotFoundError("run was not found")
        return dict(row)

    @staticmethod
    def _latest_cursor(session: Session, tenant_id: UUID, run_id: UUID) -> str:
        row = session.execute(
            select(run_status_history.c.id)
            .where(run_status_history.c.tenant_id == tenant_id, run_status_history.c.run_id == run_id)
            .order_by(run_status_history.c.occurred_at.desc(), run_status_history.c.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        return _cursor(row) if row is not None else "event.initial"

    @staticmethod
    def _evidence_chain(
        session: Session, tenant_id: UUID, decision_id: UUID, report_id: UUID
    ) -> list[dict[str, object]]:
        rows = (
            session.execute(
                select(
                    finding.c.id.label("finding_id"),
                    evidence.c.id.label("evidence_id"),
                    evidence.c.object_key,
                    evidence.c.sha256,
                    evidence.c.source_type,
                    evidence.c.trust_level,
                    evidence.c.summary,
                    evidence.c.region,
                    evidence.c.fetched_at,
                    evidence.c.valid_until,
                    finding.c.dimension_code,
                    finding.c.structured_result,
                )
                .join(
                    decision_finding,
                    (decision_finding.c.tenant_id == finding.c.tenant_id)
                    & (decision_finding.c.finding_id == finding.c.id),
                )
                .join(
                    finding_evidence,
                    (finding_evidence.c.tenant_id == finding.c.tenant_id)
                    & (finding_evidence.c.finding_id == finding.c.id),
                )
                .join(
                    evidence,
                    (evidence.c.tenant_id == finding_evidence.c.tenant_id)
                    & (evidence.c.id == finding_evidence.c.evidence_id),
                )
                .where(decision_finding.c.tenant_id == tenant_id, decision_finding.c.decision_id == decision_id)
            )
            .mappings()
            .all()
        )
        return [
            {
                "report_id": str(report_id),
                "decision_id": str(decision_id),
                "finding_id": str(row["finding_id"]),
                "evidence_id": str(row["evidence_id"]),
                "object_key": row["object_key"],
                "sha256": row["sha256"],
                "source_type": row["source_type"],
                "trust_level": row["trust_level"],
                "summary": row["summary"],
                "region": row["region"],
                "fetched_at": _iso(row["fetched_at"]) if row["fetched_at"] else None,
                "valid_until": _iso(row["valid_until"]) if row["valid_until"] else None,
                "dimension": row["dimension_code"],
                "structured_result": row["structured_result"],
            }
            for row in rows
        ]

    @staticmethod
    def _dimension_results(
        grades: dict[str, str], baseline: dict[str, str] | None, chain: list[dict[str, object]]
    ) -> dict[str, dict[str, object]]:
        grade_rank = {"INSUFFICIENT_EVIDENCE": 0, "WEAK": 1, "MODERATE": 2, "STRONG": 3}
        trust_rank = {f"E{value}": value for value in range(6)}
        results: dict[str, dict[str, object]] = {}
        for dimension, grade in grades.items():
            linked = [item for item in chain if item["dimension"] == dimension]
            trust = min((trust_rank.get(str(item["trust_level"]), 0) for item in linked), default=0)
            if baseline is None or dimension not in baseline:
                change = "NO_BASELINE"
            else:
                delta = grade_rank.get(grade, 0) - grade_rank.get(baseline[dimension], 0)
                change = "IMPROVED" if delta > 0 else ("REGRESSED" if delta < 0 else "UNCHANGED")
            results[dimension] = {
                "grade": grade,
                "evidence_confidence": f"E{trust}",
                "supporting_evidence": [item["evidence_id"] for item in linked],
                "counter_evidence": [],
                "change": change,
                "region": ExperienceReadApplication._first_claim_value(linked, "region")
                or next((item["region"] for item in linked if item["region"]), None),
                "as_of": ExperienceReadApplication._first_claim_value(linked, "fetched_at")
                or next((item["fetched_at"] for item in linked if item["fetched_at"]), None),
                "valid_until": ExperienceReadApplication._first_claim_value(linked, "valid_until")
                or next((item["valid_until"] for item in linked if item["valid_until"]), None),
                "trend_signal": ExperienceReadApplication._first_claim_value(linked, "trend_signal"),
            }
        return results

    @staticmethod
    def _action_links(
        actions: list[str], dimensions: dict[str, dict[str, object]]
    ) -> list[dict[str, object]]:
        rank = {"INSUFFICIENT_EVIDENCE": 0, "WEAK": 1, "MODERATE": 2, "STRONG": 3}
        weakest = sorted(dimensions, key=lambda item: (rank.get(str(dimensions[item]["grade"]), 0), item))
        return [
            {
                "action": action,
                "dimension": weakest[min(index, len(weakest) - 1)] if weakest else None,
                "evidence_ids": dimensions[weakest[min(index, len(weakest) - 1)]]["supporting_evidence"]
                if weakest else [],
            }
            for index, action in enumerate(actions[:3])
        ]

    @staticmethod
    def _first_claim_value(linked: list[dict[str, object]], key: str) -> object | None:
        for item in linked:
            structured = item.get("structured_result")
            claim = structured.get("claim") if isinstance(structured, dict) else None
            if isinstance(claim, dict) and claim.get(key) is not None:
                return claim[key]
        return None

    @staticmethod
    def _geo_trend(dimensions: dict[str, dict[str, object]]) -> dict[str, object]:
        item = dimensions.get("GEO_POLICY_TREND", {})
        signal = item.get("trend_signal") or "UNKNOWN"
        return {
            "signal": signal, "region": item.get("region"), "as_of": item.get("as_of"),
            "valid_until": item.get("valid_until"),
            "evidence_ids": item.get("supporting_evidence", []),
        }


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


__all__ = ["CursorInvalidError", "ExperienceReadApplication", "RunEvent"]
