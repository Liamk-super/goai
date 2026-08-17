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
    agent_report_artifact,
    agentteams_run_binding,
    budget_reservation,
    decision,
    decision_finding,
    evaluation_run,
    evidence,
    evidence_audit,
    finding,
    finding_evidence,
    manager_synthesis,
    matrix_event_receipt,
    matrix_handoff,
    outbox_message,
    product_profile,
    product_version,
    project,
    report,
    run_execution_control,
    run_execution_event,
    run_manifest,
    run_status_history,
    skill_invocation,
    stage,
    task,
    tool_invocation,
    workspace_member,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.modules.identity_tenant.application import Actor, AuthorizationError, NotFoundError
from launchscope_api.modules.supervisor.generation import is_supervisor_generation
from launchscope_domain.value_objects import TenantScope


class CursorInvalidError(ValueError):
    """The SSE cursor is not a durable cursor for the requested tenant/run."""


_AGENT_REPORTS = (
    ("user-evidence", "用户报告", "DOMAIN"),
    ("product-engineering", "产品经理报告", "DOMAIN"),
    ("business-investment", "投资人报告", "DOMAIN"),
    ("evidence-auditor", "证据审核报告", "AUDIT"),
)


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

    def project_portrait(self, actor: Actor, project_id: UUID) -> dict[str, object]:
        with self._session(actor) as session:
            visible_project = session.execute(
                select(project.c.id)
                .join(
                    workspace_member,
                    (workspace_member.c.tenant_id == project.c.tenant_id)
                    & (workspace_member.c.workspace_id == project.c.workspace_id),
                )
                .where(
                    project.c.id == project_id,
                    project.c.tenant_id == actor.tenant_id,
                    workspace_member.c.actor_id == actor.actor_id,
                )
                .limit(1)
            ).scalar_one_or_none()
            if visible_project is None:
                raise NotFoundError("project was not found")
            row = session.execute(
                select(
                    product_version.c.id.label("product_version_id"),
                    product_version.c.label.label("version_label"),
                    product_version.c.version_number,
                    product_profile.c.confirmed_at,
                    product_profile.c.confirmed_fields,
                )
                .join(
                    product_profile,
                    (product_profile.c.tenant_id == product_version.c.tenant_id)
                    & (product_profile.c.product_version_id == product_version.c.id),
                )
                .where(
                    product_version.c.tenant_id == actor.tenant_id,
                    product_version.c.project_id == project_id,
                    product_profile.c.confirmation_status == "CONFIRMED",
                )
                .order_by(
                    product_version.c.version_number.desc(),
                    product_profile.c.confirmed_at.desc(),
                    product_profile.c.id.desc(),
                )
                .limit(1)
            ).mappings().first()
            if row is None:
                return {
                    "project_id": str(project_id),
                    "product_version_id": None,
                    "version_label": None,
                    "version_number": None,
                    "confirmed_at": None,
                    "confirmed_fields": {},
                }
            return {
                "project_id": str(project_id),
                "product_version_id": str(row["product_version_id"]),
                "version_label": row["version_label"],
                "version_number": int(row["version_number"]),
                "confirmed_at": _iso(cast(datetime, row["confirmed_at"])),
                "confirmed_fields": dict(row["confirmed_fields"] or {}),
            }

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

    def evaluation_history(
        self,
        actor: Actor,
        *,
        limit: int = 20,
        offset: int = 0,
        search: str = "",
        sort: str = "newest",
    ) -> dict[str, object]:
        with self._session(actor) as session:
            conditions = [
                evaluation_run.c.tenant_id == actor.tenant_id,
                workspace_member.c.actor_id == actor.actor_id,
            ]
            if search.strip():
                conditions.append(project.c.name.ilike(f"%{search.strip()}%"))
            latest_recommendation = (
                select(decision.c.recommendation)
                .where(
                    decision.c.tenant_id == evaluation_run.c.tenant_id,
                    decision.c.run_id == evaluation_run.c.id,
                )
                .order_by(decision.c.created_at.desc())
                .limit(1)
                .correlate(evaluation_run)
                .scalar_subquery()
            )
            base = (
                select(
                    evaluation_run.c.id.label("run_id"),
                    evaluation_run.c.project_id,
                    evaluation_run.c.status,
                    evaluation_run.c.updated_at,
                    project.c.name.label("project_name"),
                    product_version.c.label.label("product_version_label"),
                    product_version.c.version_number.label("product_version_number"),
                    latest_recommendation.label("recommendation"),
                )
                .join(
                    project,
                    (project.c.tenant_id == evaluation_run.c.tenant_id) & (project.c.id == evaluation_run.c.project_id),
                )
                .join(
                    workspace_member,
                    (workspace_member.c.tenant_id == project.c.tenant_id)
                    & (workspace_member.c.workspace_id == project.c.workspace_id),
                )
                .join(
                    product_version,
                    (product_version.c.tenant_id == evaluation_run.c.tenant_id)
                    & (product_version.c.id == evaluation_run.c.product_version_id),
                )
                .where(*conditions)
            )
            count_statement = (
                select(func.count(func.distinct(evaluation_run.c.id)))
                .select_from(evaluation_run)
                .join(
                    project,
                    (project.c.tenant_id == evaluation_run.c.tenant_id) & (project.c.id == evaluation_run.c.project_id),
                )
                .join(
                    workspace_member,
                    (workspace_member.c.tenant_id == project.c.tenant_id)
                    & (workspace_member.c.workspace_id == project.c.workspace_id),
                )
                .where(*conditions)
            )
            total = int(session.execute(count_statement).scalar_one())
            order = evaluation_run.c.updated_at.asc() if sort == "oldest" else evaluation_run.c.updated_at.desc()
            rows = session.execute(
                base.order_by(order, evaluation_run.c.id.desc()).offset(offset).limit(limit)
            ).mappings()
            items = [
                {
                    "run_id": str(row["run_id"]),
                    "project_id": str(row["project_id"]),
                    "project_name": row["project_name"],
                    "product_version_label": row["product_version_label"],
                    "product_version_number": row["product_version_number"],
                    "status": row["status"],
                    "recommendation": row["recommendation"],
                    "updated_at": _iso(row["updated_at"]),
                }
                for row in rows
            ]
            return {"items": items, "total": total, "has_more": offset + len(items) < total}

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
            control_events = (
                session.execute(
                    select(
                        run_execution_event.c.id,
                        run_execution_event.c.event_type,
                        run_execution_event.c.control_state,
                        run_execution_event.c.control_epoch,
                        run_execution_event.c.data,
                        run_execution_event.c.occurred_at,
                    )
                    .where(
                        run_execution_event.c.tenant_id == actor.tenant_id,
                        run_execution_event.c.run_id == run_id,
                    )
                    .order_by(run_execution_event.c.occurred_at, run_execution_event.c.id)
                )
                .mappings()
                .all()
            )
            combined = [
                {
                    "id": row["id"],
                    "event_type": "run.status_changed",
                    "occurred_at": row["occurred_at"],
                    "data": {
                        "run_id": str(run_id),
                        "status": row["to_status"],
                        "reason": row["reason"],
                        "failure_class": row["failure_class"],
                        "occurred_at": _iso(row["occurred_at"]),
                    },
                }
                for row in ordered
            ]
            combined.extend(
                {
                    "id": row["id"],
                    "event_type": row["event_type"],
                    "occurred_at": row["occurred_at"],
                    "data": {
                        "run_id": str(run_id),
                        "control_state": row["control_state"],
                        "control_epoch": row["control_epoch"],
                        "occurred_at": _iso(row["occurred_at"]),
                        **dict(row["data"] or {}),
                    },
                }
                for row in control_events
            )
            combined.sort(key=lambda item: (item["occurred_at"], item["id"]))
            if cursor == "event.initial":
                after_index = -1
            else:
                after_id = _parse_cursor(cursor)
                positions = {row["id"]: index for index, row in enumerate(combined)}
                if after_id not in positions:
                    raise CursorInvalidError("cursor does not belong to this durable run event stream")
                after_index = positions[after_id]
            return snapshot, tuple(
                RunEvent(
                    cursor=_cursor(row["id"]),
                    event_type=str(row["event_type"]),
                    data=dict(row["data"]),
                )
                for row in combined[after_index + 1 :]
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
                    (evaluation_run.c.tenant_id == decision.c.tenant_id) & (evaluation_run.c.id == decision.c.run_id),
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
            calibration_rows = (
                session.execute(
                    select(
                        evidence_audit.c.finding_id,
                        evidence_audit.c.decision,
                        evidence_audit.c.reason,
                        evidence_audit.c.contract_version,
                        evidence_audit.c.rule_ids,
                        evidence_audit.c.referenced_evidence_ids,
                        evidence_audit.c.score_components,
                        evidence_audit.c.flags,
                    )
                    .where(evidence_audit.c.tenant_id == actor.tenant_id, evidence_audit.c.run_id == run_id)
                    .order_by(evidence_audit.c.audited_at)
                )
                .mappings()
                .all()
            )
            synthesis_row = (
                session.execute(
                    select(manager_synthesis.c.raw_synthesis, manager_synthesis.c.status)
                    .where(manager_synthesis.c.tenant_id == actor.tenant_id, manager_synthesis.c.run_id == run_id)
                    .order_by(manager_synthesis.c.created_at.desc(), manager_synthesis.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            score_document = decision_row["dimension_grades"]
            layered_report = self._layered_report(score_document, synthesis_row)
            state_flags = run_row.get("state_flags")
            return {
                "report_id": str(report_row["id"]),
                "run_id": str(run_id),
                "project_id": str(run_row["project_id"]),
                "project_name": run_row.get("project_name"),
                "locale": (state_flags.get("locale") or "zh-CN") if isinstance(state_flags, dict) else "zh-CN",
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
                    dimension
                    for dimension, result in dimension_results.items()
                    if result["grade"] == "INSUFFICIENT_EVIDENCE"
                ],
                "calibration_results": [
                    {
                        "finding_id": str(item["finding_id"]),
                        "decision": item["decision"],
                        "reason": item["reason"],
                        "contract_version": item["contract_version"],
                        "rule_ids": item["rule_ids"],
                        "evidence_ids": item["referenced_evidence_ids"],
                        "score_components": item["score_components"],
                        "flags": item["flags"],
                    }
                    for item in calibration_rows
                ],
                "created_at": _iso(report_row["created_at"]),
                "evidence_chain": chain,
                "architecture_generation": self._manifest_generation(session, actor.tenant_id, run_id),
                "deterministic_score": score_document if layered_report is not None else None,
                "layered_report": layered_report,
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

    def report_v2_metadata(self, actor: Actor, run_id: UUID) -> dict[str, object]:
        with self._session(actor) as session:
            run_row = self._run_row(session, actor, run_id)
            self._require_report_v2_run(run_row)
            row = (
                session.execute(
                    select(report)
                    .where(
                        report.c.tenant_id == actor.tenant_id,
                        report.c.run_id == run_id,
                        report.c.status == "COMMITTED",
                    )
                    .order_by(report.c.created_at.desc(), report.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFoundError("no immutable v2 report has been committed for this run")
            return self._report_object_metadata(row)

    def report_v2_metadata_by_id(self, actor: Actor, report_id: UUID) -> dict[str, object]:
        with self._session(actor) as session:
            row = (
                session.execute(
                    select(report).where(
                        report.c.tenant_id == actor.tenant_id,
                        report.c.id == report_id,
                        report.c.status == "COMMITTED",
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFoundError("immutable v2 report was not found")
            run_row = self._run_row(session, actor, row["run_id"])
            self._require_report_v2_run(run_row)
            return self._report_object_metadata(row)

    def report_v3_metadata(self, actor: Actor, run_id: UUID) -> dict[str, object]:
        with self._session(actor) as session:
            run_row = self._run_row(session, actor, run_id)
            self._require_report_v3_run(run_row)
            row = (
                session.execute(
                    select(report)
                    .where(
                        report.c.tenant_id == actor.tenant_id,
                        report.c.run_id == run_id,
                        report.c.status == "COMMITTED",
                    )
                    .order_by(report.c.created_at.desc(), report.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFoundError("no immutable v3 report has been committed for this run")
            return self._report_object_metadata(row)

    def report_v3_metadata_by_id(self, actor: Actor, report_id: UUID) -> dict[str, object]:
        with self._session(actor) as session:
            row = (
                session.execute(
                    select(report).where(
                        report.c.tenant_id == actor.tenant_id,
                        report.c.id == report_id,
                        report.c.status == "COMMITTED",
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFoundError("immutable v3 report was not found")
            run_row = self._run_row(session, actor, row["run_id"])
            self._require_report_v3_run(run_row)
            return self._report_object_metadata(row)

    def agentteams_run(self, actor: Actor, run_id: UUID) -> dict[str, object]:
        """Body-free live topology and progress projection for the v0.2 Run page."""

        with self._session(actor) as session:
            run_row = self._run_row(session, actor, run_id)
            binding = (
                session.execute(
                    select(agentteams_run_binding).where(
                        agentteams_run_binding.c.tenant_id == actor.tenant_id,
                        agentteams_run_binding.c.run_id == run_id,
                    )
                )
                .mappings()
                .first()
            )
            tasks = (
                session.execute(
                    select(
                        task.c.id,
                        task.c.stage_code,
                        task.c.agent_identity_ref,
                        task.c.status,
                        task.c.tool_allowlist,
                        task.c.evidence_requirement,
                        task.c.last_error,
                    task.c.last_failure_class,
                    task.c.side_effect_started,
                    task.c.created_at,
                    task.c.updated_at,
                    )
                    .where(task.c.tenant_id == actor.tenant_id, task.c.run_id == run_id)
                    .order_by(task.c.created_at, task.c.id)
                )
                .mappings()
                .all()
            )
            stages = (
                session.execute(
                    select(
                        stage.c.code,
                        stage.c.ordinal,
                        stage.c.status,
                        stage.c.started_at,
                        stage.c.completed_at,
                    )
                    .where(stage.c.tenant_id == actor.tenant_id, stage.c.run_id == run_id)
                    .order_by(stage.c.ordinal)
                )
                .mappings()
                .all()
            )
            handoff_count = (
                session.execute(
                    select(matrix_handoff.c.id).where(
                        matrix_handoff.c.tenant_id == actor.tenant_id,
                        matrix_handoff.c.run_id == run_id,
                    )
                )
                .all()
                .__len__()
            )
            receipt_count = (
                session.execute(
                    select(matrix_event_receipt.c.id).where(
                        matrix_event_receipt.c.tenant_id == actor.tenant_id,
                        matrix_event_receipt.c.run_id == run_id,
                    )
                )
                .all()
                .__len__()
            )
            budget = (
                session.execute(
                    select(
                        budget_reservation.c.limit_amount,
                        budget_reservation.c.consumed_amount,
                        budget_reservation.c.status,
                    ).where(
                        budget_reservation.c.tenant_id == actor.tenant_id,
                        budget_reservation.c.run_id == run_id,
                        budget_reservation.c.category == "run_total",
                    )
                )
                .mappings()
                .first()
            )
            return {
                "run_id": str(run_id),
                "team": {
                    "name": binding["team_name"] if binding else "launchscope-potential-review",
                    "agentteams_version": binding["agentteams_version"] if binding else "v1.2.0",
                    "binding_status": binding["binding_status"] if binding else "NOT_DISPATCHED",
                    "team_room_id": binding["team_room_id"] if binding else None,
                },
                "stages": [self._stage_projection(item, run_row) for item in stages],
                "tasks": [self._task_projection(session, actor.tenant_id, item) for item in tasks],
                "handoff_count": handoff_count,
                "matrix_event_count": receipt_count,
                "budget": {
                    "currency": "USD",
                    "limit": str(budget["limit_amount"]),
                    "consumed": str(budget["consumed_amount"]),
                    "status": budget["status"],
                }
                if budget
                else None,
            }

    @staticmethod
    def _stage_projection(item: Any, run_row: dict[str, object]) -> dict[str, object]:
        completed_run = run_row["status"] == "COMPLETED"
        completed_at = item["completed_at"] or (run_row["updated_at"] if completed_run else None)
        return {
            **dict(item),
            "status": "COMPLETED" if completed_run else item["status"],
            "started_at": _iso(cast(datetime, item["started_at"])) if item["started_at"] else None,
            "completed_at": _iso(cast(datetime, completed_at)) if completed_at else None,
        }

    def agent_report_summaries(self, actor: Actor, run_id: UUID) -> dict[str, object]:
        with self._session(actor) as session:
            self._run_row(session, actor, run_id)
            task_rows = (
                session.execute(
                    select(
                        task.c.agent_identity_ref,
                        task.c.status,
                        task.c.last_error,
                        task.c.updated_at,
                    ).where(task.c.tenant_id == actor.tenant_id, task.c.run_id == run_id)
                )
                .mappings()
                .all()
            )
            artifact_rows = (
                session.execute(
                    select(agent_report_artifact)
                    .where(
                        agent_report_artifact.c.tenant_id == actor.tenant_id,
                        agent_report_artifact.c.run_id == run_id,
                    )
                    .order_by(
                        agent_report_artifact.c.revision.desc(),
                        agent_report_artifact.c.created_at.desc(),
                    )
                )
                .mappings()
                .all()
            )
            latest_artifact: dict[str, Any] = {}
            for artifact in artifact_rows:
                latest_artifact.setdefault(str(artifact["agent_code"]), artifact)
            task_by_agent: dict[str, list[Any]] = {}
            for task_row in task_rows:
                agent = str(task_row["agent_identity_ref"]).split("@", 1)[0]
                task_by_agent.setdefault(agent, []).append(task_row)

            reports: list[dict[str, object]] = []
            for agent_code, title, report_kind in _AGENT_REPORTS:
                selected_artifact = latest_artifact.get(agent_code)
                agent_tasks = task_by_agent.get(agent_code, [])
                if selected_artifact is not None:
                    status = "AVAILABLE"
                elif any(item["status"] == "SUCCEEDED" for item in agent_tasks):
                    status = "UNAVAILABLE"
                elif agent_tasks and all(
                    item["status"] in {"KNOWN_FAILED", "FAILED", "NEEDS_ATTENTION"} for item in agent_tasks
                ):
                    status = "FAILED"
                else:
                    status = "PENDING"
                failure = next((str(item["last_error"]) for item in agent_tasks if item["last_error"]), None)
                reports.append(
                    {
                        "agent_code": agent_code,
                        "title": title,
                        "kind": report_kind,
                        "status": status,
                        "report_id": str(selected_artifact["id"]) if selected_artifact is not None else None,
                        "sha256": str(selected_artifact["sha256"]) if selected_artifact is not None else None,
                        "source_sha256": str(selected_artifact["sha256"]) if selected_artifact is not None else None,
                        "created_at": _iso(selected_artifact["created_at"]) if selected_artifact is not None else None,
                        "revision": int(selected_artifact["revision"]) if selected_artifact is not None else None,
                        "failure_reason": failure,
                    }
                )
            return {"run_id": str(run_id), "reports": reports}

    def agent_report_summaries_v2(self, actor: Actor, run_id: UUID) -> dict[str, object]:
        with self._session(actor) as session:
            self._require_report_v2_run(self._run_row(session, actor, run_id))
        return self.agent_report_summaries(actor, run_id)

    def agent_report_summaries_v3(self, actor: Actor, run_id: UUID) -> dict[str, object]:
        with self._session(actor) as session:
            self._require_report_v3_run(self._run_row(session, actor, run_id))
        return self.agent_report_summaries(actor, run_id)

    def agent_report_metadata(self, actor: Actor, run_id: UUID, agent_code: str) -> dict[str, object]:
        slot = next((item for item in _AGENT_REPORTS if item[0] == agent_code), None)
        if slot is None:
            raise NotFoundError("Agent report was not found")
        with self._session(actor) as session:
            self._run_row(session, actor, run_id)
            artifact = (
                session.execute(
                    select(agent_report_artifact)
                    .where(
                        agent_report_artifact.c.tenant_id == actor.tenant_id,
                        agent_report_artifact.c.run_id == run_id,
                        agent_report_artifact.c.agent_code == agent_code,
                        agent_report_artifact.c.status == "AVAILABLE",
                    )
                    .order_by(
                        agent_report_artifact.c.revision.desc(),
                        agent_report_artifact.c.created_at.desc(),
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if artifact is None:
                raise NotFoundError("Agent report is not available")
            return {
                "report_id": str(artifact["id"]),
                "run_id": str(run_id),
                "agent_code": agent_code,
                "title": slot[1],
                "kind": slot[2],
                "object_key": str(artifact["object_key"]),
                "sha256": str(artifact["sha256"]),
                "mime_type": str(artifact["mime_type"]),
                "created_at": _iso(artifact["created_at"]),
                "audit_round": int(artifact["revision"]) if slot[2] == "AUDIT" else None,
                "revision": int(artifact["revision"]),
                "source_sha256": str(artifact["sha256"]),
            }

    def agent_report_metadata_v2(self, actor: Actor, run_id: UUID, agent_code: str) -> dict[str, object]:
        with self._session(actor) as session:
            self._require_report_v2_run(self._run_row(session, actor, run_id))
            supervisor_report_id = session.execute(
                select(report.c.id)
                .where(
                    report.c.tenant_id == actor.tenant_id,
                    report.c.run_id == run_id,
                    report.c.status == "COMMITTED",
                )
                .order_by(report.c.created_at.desc(), report.c.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if supervisor_report_id is None:
                raise NotFoundError("supervisor report is not available")
        metadata = self.agent_report_metadata(actor, run_id, agent_code)
        metadata["supervisor_report_id"] = str(supervisor_report_id)
        return metadata

    def agent_report_metadata_v3(self, actor: Actor, run_id: UUID, agent_code: str) -> dict[str, object]:
        with self._session(actor) as session:
            self._require_report_v3_run(self._run_row(session, actor, run_id))
            supervisor_report_id = session.execute(
                select(report.c.id)
                .where(
                    report.c.tenant_id == actor.tenant_id,
                    report.c.run_id == run_id,
                    report.c.status == "COMMITTED",
                )
                .order_by(report.c.created_at.desc(), report.c.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if supervisor_report_id is None:
                raise NotFoundError("supervisor report is not available")
        metadata = self.agent_report_metadata(actor, run_id, agent_code)
        metadata["supervisor_report_id"] = str(supervisor_report_id)
        return metadata

    def domain_agent_report_projection(self, actor: Actor, run_id: UUID, agent_code: str) -> dict[str, object]:
        if agent_code not in {item[0] for item in _AGENT_REPORTS if item[2] == "DOMAIN"}:
            raise NotFoundError("Domain Agent report was not found")
        with self._session(actor) as session:
            self._run_row(session, actor, run_id)
            artifact = (
                session.execute(
                    select(agent_report_artifact)
                    .where(
                        agent_report_artifact.c.tenant_id == actor.tenant_id,
                        agent_report_artifact.c.run_id == run_id,
                        agent_report_artifact.c.agent_code == agent_code,
                        agent_report_artifact.c.status == "AVAILABLE",
                    )
                    .order_by(
                        agent_report_artifact.c.revision.desc(),
                        agent_report_artifact.c.created_at.desc(),
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if artifact is None:
                raise NotFoundError("Domain Agent report is not available")
            rows = (
                session.execute(
                    select(finding)
                    .where(
                        finding.c.tenant_id == actor.tenant_id,
                        finding.c.run_id == run_id,
                        finding.c.task_id == artifact["task_id"],
                        finding.c.submitted_by == agent_code,
                    )
                    .order_by(finding.c.submitted_at, finding.c.id)
                )
                .mappings()
                .all()
            )
            findings = [dict(item["structured_result"]["finding"]) for item in rows]
            limitations = list(
                dict.fromkeys(
                    str(value) for item in findings for value in cast(list[object], item.get("limitations", []))
                )
            )
            confidence_values = [float(item["confidence"]) for item in findings if item.get("confidence") is not None]
            return {
                "schema_version": "DomainAgentReportViewV1",
                "run_id": str(run_id),
                "task_id": str(artifact["task_id"]),
                "agent_code": agent_code,
                "dispatch_epoch": int(artifact["revision"]),
                "status": "AVAILABLE",
                "confidence": sum(confidence_values) / len(confidence_values) if confidence_values else None,
                "findings": findings,
                "limitations": limitations,
                "next_action": None,
                "evidence_refs": [
                    reference for item in findings for reference in cast(list[object], item.get("evidence_refs", []))
                ],
                "submitted_report_ref": {
                    "ref": str(artifact["object_key"]),
                    "sha256": str(artifact["sha256"]),
                    "mime_type": str(artifact["mime_type"]),
                },
                "projection_note": "Readable projection reconstructed from immutable domain findings.",
            }

    @staticmethod
    def _task_projection(session: Session, tenant_id: UUID, item: Any) -> dict[str, object]:
        evidence_count = session.execute(
            select(func.count())
            .select_from(evidence)
            .where(evidence.c.tenant_id == tenant_id, evidence.c.task_id == item["id"])
        ).scalar_one()
        tools = (
            session.execute(
                select(tool_invocation.c.tool_code, tool_invocation.c.status)
                .join(
                    skill_invocation,
                    (skill_invocation.c.tenant_id == tool_invocation.c.tenant_id)
                    & (skill_invocation.c.id == tool_invocation.c.skill_invocation_id),
                )
                .where(skill_invocation.c.tenant_id == tenant_id, skill_invocation.c.task_id == item["id"])
                .order_by(tool_invocation.c.created_at)
            )
            .mappings()
            .all()
        )
        result = dict(item)
        result.update(
            id=str(item["id"]),
            summary=item["evidence_requirement"],
            evidence_count=evidence_count,
            failure_reason=item["last_error"],
            retryable=(item["last_failure_class"] in {"TRANSIENT", "VALIDATION"} and not item["side_effect_started"]),
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
                    (evaluation_run.c.tenant_id == evidence.c.tenant_id) & (evaluation_run.c.id == evidence.c.run_id),
                )
                .join(
                    project,
                    (project.c.tenant_id == evaluation_run.c.tenant_id) & (project.c.id == evaluation_run.c.project_id),
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
            if candidate["status"] != "COMPLETED":
                raise NotFoundError("a completed candidate run with a decision is required for comparison")
            baseline = (
                session.execute(
                    self._visible_runs(actor)
                    .join(
                        decision,
                        (decision.c.tenant_id == evaluation_run.c.tenant_id)
                        & (decision.c.run_id == evaluation_run.c.id),
                    )
                    .where(
                        evaluation_run.c.project_id == project_id,
                        evaluation_run.c.id != run_id,
                        evaluation_run.c.status == "COMPLETED",
                        evaluation_run.c.created_at < candidate["created_at"],
                    )
                    .order_by(evaluation_run.c.created_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if baseline is None:
                raise NotFoundError("a prior completed run with a decision is required for comparison")
            comparable = baseline["standard_version"] == candidate["standard_version"]
            grade_rows = session.execute(
                select(decision.c.run_id, decision.c.dimension_grades)
                .where(
                    decision.c.tenant_id == actor.tenant_id,
                    decision.c.run_id.in_((baseline["id"], candidate["id"])),
                )
                .order_by(decision.c.created_at.desc())
            ).all()
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

    @staticmethod
    def _report_object_metadata(row: Any) -> dict[str, object]:
        return {
            "report_id": str(row["id"]),
            "run_id": str(row["run_id"]),
            "object_key": str(row["object_key"]),
            "sha256": str(row["sha256"]),
            "mime_type": "application/json",
            "created_at": _iso(row["created_at"]),
        }

    @staticmethod
    def _require_report_v2_run(row: Any) -> None:
        generation = ExperienceReadApplication._architecture_generation(row)
        if generation != "supervisor-1p4-report-v22" or row.get("report_profile_ref") != "supervisor-report@2.0":
            raise NotFoundError("immutable v2 report was not found")

    @staticmethod
    def _require_report_v3_run(row: Any) -> None:
        generation = ExperienceReadApplication._architecture_generation(row)
        if generation != "supervisor-1p4-report-v3" or row.get("report_profile_ref") != "supervisor-report@3.0":
            raise NotFoundError("immutable v3 report was not found")

    def _ops_session(self) -> Session:
        if self._ops_sessions is None:
            raise AuthorizationError("Ops projection database role is not configured")
        return self._ops_sessions()

    @staticmethod
    def _run_projection(row: Any, cursor: str) -> dict[str, object]:
        generation = ExperienceReadApplication._architecture_generation(row)
        return {
            "run_id": str(row["id"]),
            "project_id": str(row["project_id"]),
            "project_name": row.get("project_name"),
            "product_version_id": str(row["product_version_id"]),
            "product_version_label": row.get("product_version_label"),
            "product_version_number": row.get("product_version_number"),
            "status": row["status"],
            "standard_version": row["standard_version"],
            "current_cursor": cursor,
            "correlation_id": str(row["correlation_id"]),
            "current_stage": row["current_stage"],
            "attention_reason": row["attention_reason"],
            "updated_at": _iso(cast(datetime, row["updated_at"])),
            "architecture_generation": generation,
            "ui_mode": "SUPERVISOR_1P4" if is_supervisor_generation(generation) else "LEGACY",
            "dispatch_pending": bool((row.get("state_flags") or {}).get("dispatch_pending")),
            "locale": (row.get("state_flags") or {}).get("locale") or "zh-CN",
            "execution_control": {
                "state": row.get("execution_control_state") or "ACTIVE",
                "control_epoch": int(row.get("execution_control_epoch") or 0),
                "usage_settlement_status": row.get("usage_settlement_status") or "NONE",
                "in_flight_count": int(row.get("execution_in_flight_count") or 0),
                "pause_requested_at": _iso(row["pause_requested_at"]) if row.get("pause_requested_at") else None,
                "paused_at": _iso(row["paused_at"]) if row.get("paused_at") else None,
                "resumed_at": _iso(row["resumed_at"]) if row.get("resumed_at") else None,
                "last_error": row.get("execution_control_error"),
            },
            "experience_stage": ExperienceReadApplication._experience_stage(
                str(row["status"]), row["current_stage"], row.get("state_flags") or {}
            ),
        }

    def _visible_runs(self, actor: Actor) -> Select[Any]:
        return (
            select(
                evaluation_run,
                product_version.c.label.label("product_version_label"),
                product_version.c.version_number.label("product_version_number"),
                project.c.name.label("project_name"),
                run_manifest.c.frozen_config.label("manifest_frozen_config"),
                run_execution_control.c.state.label("execution_control_state"),
                run_execution_control.c.control_epoch.label("execution_control_epoch"),
                run_execution_control.c.usage_settlement_status,
                run_execution_control.c.in_flight_count.label("execution_in_flight_count"),
                run_execution_control.c.pause_requested_at,
                run_execution_control.c.paused_at,
                run_execution_control.c.resumed_at,
                run_execution_control.c.last_error.label("execution_control_error"),
            )
            .join(
                project,
                (project.c.tenant_id == evaluation_run.c.tenant_id) & (project.c.id == evaluation_run.c.project_id),
            )
            .join(
                workspace_member,
                (workspace_member.c.tenant_id == project.c.tenant_id)
                & (workspace_member.c.workspace_id == project.c.workspace_id),
            )
            .join(
                product_version,
                (product_version.c.tenant_id == evaluation_run.c.tenant_id)
                & (product_version.c.id == evaluation_run.c.product_version_id),
            )
            .outerjoin(
                run_manifest,
                (run_manifest.c.tenant_id == evaluation_run.c.tenant_id)
                & (run_manifest.c.run_id == evaluation_run.c.id),
            )
            .outerjoin(
                run_execution_control,
                (run_execution_control.c.tenant_id == evaluation_run.c.tenant_id)
                & (run_execution_control.c.run_id == evaluation_run.c.id),
            )
            .where(evaluation_run.c.tenant_id == actor.tenant_id, workspace_member.c.actor_id == actor.actor_id)
            .order_by(evaluation_run.c.updated_at.desc(), evaluation_run.c.id.desc())
        )

    @staticmethod
    def _architecture_generation(row: Any) -> str:
        manifest = row.get("manifest_frozen_config") or {}
        generation = manifest.get("architecture_generation") if isinstance(manifest, dict) else None
        if generation:
            return str(generation)
        flags = row.get("state_flags") or {}
        persisted = flags.get("architecture_generation") if isinstance(flags, dict) else None
        return str(persisted) if persisted else "legacy-1p5"

    @staticmethod
    def _experience_stage(status: str, current_stage: object, state_flags: dict[str, object]) -> dict[str, object]:
        stage = str(current_stage or "")
        if status == "COMPLETED" or stage == "COMPLETED":
            ordinal, code, label = 4, "COMPLETED", "预测完成"
        elif stage in {
            "EVIDENCE_AUDIT",
            "TARGETED_REMEDIATION",
            "REAUDIT",
            "DETERMINISTIC_SCORING",
            "SUPERVISOR_SYNTHESIS",
            "REPORT_COMMIT",
            "RULE_SYNTHESIS",
        }:
            ordinal, code, label = 3, "REVIEW_REPORT", "证据校准与结果汇总"
        elif stage in {"DOMAIN_REVIEW"}:
            ordinal, code, label = 2, "MULTI_REVIEW", "多维预测"
        else:
            ordinal, code, label = 1, "UNDERSTANDING", "正在了解项目"
        if bool(state_flags.get("dispatch_pending")):
            label = "等待执行服务"
        exception = None
        exception_label = None
        if status == "WAITING_FOR_USER" or bool(state_flags.get("waiting_for_user")):
            exception, exception_label = "NEEDS_INPUT", "需要补充信息"
        elif status in {"WAITING_FOR_APPROVAL", "NEEDS_ATTENTION"} or bool(state_flags.get("waiting_for_approval")):
            exception, exception_label = "NEEDS_CONFIRMATION", "需要确认"
        return {
            "ordinal": ordinal,
            "code": code,
            "label": label,
            "exception": exception,
            "exception_label": exception_label,
        }

    @staticmethod
    def _manifest_generation(session: Session, tenant_id: UUID, run_id: UUID) -> str:
        frozen = session.execute(
            select(run_manifest.c.frozen_config).where(
                run_manifest.c.tenant_id == tenant_id, run_manifest.c.run_id == run_id
            )
        ).scalar_one_or_none()
        if isinstance(frozen, dict) and frozen.get("architecture_generation"):
            return str(frozen["architecture_generation"])
        return "legacy-1p5"

    @staticmethod
    def _layered_report(score: object, synthesis_row: Any | None) -> dict[str, object] | None:
        if synthesis_row is None or not isinstance(score, dict) or "coverage" not in score:
            return None
        synthesis = synthesis_row["raw_synthesis"]
        if not isinstance(synthesis, dict):
            return None
        dimension_scores = score.get("dimension_scores") or {}
        evidence_quality = dimension_scores.get("evidence_quality") if isinstance(dimension_scores, dict) else None
        confidence = round(float(evidence_quality) / 100, 4) if isinstance(evidence_quality, (int, float)) else None
        conflicts = list(synthesis.get("conflicts") or [])
        missing_agents = list(score.get("missing_agents") or [])
        return {
            "summary": str(synthesis.get("summary") or ""),
            "actions": list(synthesis.get("actions") or [])[:3],
            "largest_opportunity": next(iter(synthesis.get("cross_domain_analysis") or []), None),
            "largest_risk": next(iter(synthesis.get("risks") or []), None),
            "coverage": float(score["coverage"]),
            "confidence": confidence,
            "information_gaps": [*missing_agents, *conflicts],
            "conflicts": conflicts,
            "cross_domain_analysis": list(synthesis.get("cross_domain_analysis") or []),
            "citations": list(synthesis.get("citations") or []),
            "version_changes": synthesis.get("version_changes") or {},
            "decision_conflict": bool(synthesis.get("decision_conflict")),
            "synthesis_status": str(synthesis_row["status"]),
        }

    def _run_row(self, session: Session, actor: Actor, run_id: UUID) -> dict[str, object]:
        row = session.execute(self._visible_runs(actor).where(evaluation_run.c.id == run_id)).mappings().first()
        if row is None:
            raise NotFoundError("run was not found")
        return dict(row)

    @staticmethod
    def _latest_cursor(session: Session, tenant_id: UUID, run_id: UUID) -> str:
        status_row = session.execute(
            select(run_status_history.c.id, run_status_history.c.occurred_at)
            .where(run_status_history.c.tenant_id == tenant_id, run_status_history.c.run_id == run_id)
            .order_by(run_status_history.c.occurred_at.desc(), run_status_history.c.id.desc())
            .limit(1)
        ).one_or_none()
        control_row = session.execute(
            select(run_execution_event.c.id, run_execution_event.c.occurred_at)
            .where(run_execution_event.c.tenant_id == tenant_id, run_execution_event.c.run_id == run_id)
            .order_by(run_execution_event.c.occurred_at.desc(), run_execution_event.c.id.desc())
            .limit(1)
        ).one_or_none()
        if status_row is None and control_row is None:
            return "event.initial"
        if status_row is None:
            assert control_row is not None
            return _cursor(control_row.id)
        if control_row is None:
            return _cursor(status_row.id)
        latest = control_row if control_row.occurred_at >= status_row.occurred_at else status_row
        return _cursor(latest.id)

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
        grades: dict[str, Any], baseline: dict[str, Any] | None, chain: list[dict[str, object]]
    ) -> dict[str, dict[str, object]]:
        if "dimension_scores" in grades:
            scores = grades.get("dimension_scores")
            if not isinstance(scores, dict):
                return {}
            return {
                str(dimension): {
                    "grade": "NOT_SCORED" if value is None else f"{float(value):.2f}",
                    "score": value,
                    "evidence_confidence": "DETERMINISTIC",
                    "supporting_evidence": [item["evidence_id"] for item in chain if item["dimension"] == dimension],
                    "counter_evidence": [],
                    "change": "NO_BASELINE",
                    "region": None,
                    "as_of": None,
                    "valid_until": None,
                    "trend_signal": None,
                }
                for dimension, value in scores.items()
                if dimension != "evidence_quality"
            }
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
    def _action_links(actions: list[str], dimensions: dict[str, dict[str, object]]) -> list[dict[str, object]]:
        rank = {"INSUFFICIENT_EVIDENCE": 0, "WEAK": 1, "MODERATE": 2, "STRONG": 3}
        weakest = sorted(dimensions, key=lambda item: (rank.get(str(dimensions[item]["grade"]), 0), item))

        def target_dimension(action: str, index: int) -> str | None:
            normalized = " ".join(action.upper().replace("_", " ").split())
            explicit = next(
                (dimension for dimension in dimensions if dimension.replace("_", " ") in normalized),
                None,
            )
            return explicit or (weakest[min(index, len(weakest) - 1)] if weakest else None)

        links: list[dict[str, object]] = []
        for index, action in enumerate(actions[:3]):
            dimension = target_dimension(action, index)
            links.append(
                {
                    "action": action,
                    "dimension": dimension,
                    "evidence_ids": dimensions[dimension]["supporting_evidence"] if dimension else [],
                }
            )
        return links

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
            "signal": signal,
            "region": item.get("region"),
            "as_of": item.get("as_of"),
            "valid_until": item.get("valid_until"),
            "evidence_ids": item.get("supporting_evidence", []),
        }


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


__all__ = ["CursorInvalidError", "ExperienceReadApplication", "RunEvent"]
