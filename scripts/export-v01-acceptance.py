"""Export a body-free, replayable LaunchScope recorded acceptance snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from launchscope_api.infrastructure.db.schema import (
    agent_identity,
    agent_plan,
    agent_task_ticket,
    agentteams_run_binding,
    agentteams_task_delivery,
    approval_request,
    audit_event,
    budget_reservation,
    clarification_impact_assessment,
    decision,
    evaluation_run,
    event_delivery_attempt,
    evidence,
    evidence_audit,
    finding,
    inbox_message,
    information_request,
    information_request_answer,
    manager_synthesis,
    matrix_event_receipt,
    matrix_handoff,
    outbox_message,
    project_dossier_snapshot,
    report,
    requirement_brief,
    requirement_change,
    run_manifest,
    run_status_history,
    skill_invocation,
    task,
    tool_invocation,
    trace_metadata,
    usage_record,
)
from launchscope_api.infrastructure.db.session import (
    DatabaseSettings,
    create_database_engine,
    session_factory,
    tenant_transaction,
)
from launchscope_domain.value_objects import TenantScope

V4_AGENT_CODES = {
    "evaluation-manager",
    "user-evidence",
    "product-engineering",
    "business-investment",
    "evidence-auditor",
}


def _generation(frozen_config: dict[str, object]) -> str:
    if (
        frozen_config.get("architecture_generation") == "supervisor-1p4-v1"
        or frozen_config.get("agent_contract_generation") == "v4"
    ):
        return "v4"
    return "legacy"


def _validate_manifest_topology(manifest_rows: list[dict[str, object]]) -> dict[str, str]:
    generations: dict[str, str] = {}
    for row in manifest_rows:
        frozen_config = row["frozen_config"]
        if not isinstance(frozen_config, dict):
            raise ValueError("run manifest frozen_config must be an object")
        generation = _generation(frozen_config)
        run_id = str(row["run_id"])
        generations[run_id] = generation
        if generation != "v4":
            continue
        agents = frozen_config.get("agents")
        topology = frozen_config.get("physical_topology")
        if not isinstance(agents, dict) or set(agents) != V4_AGENT_CODES:
            raise ValueError(f"generation-v4 Run {run_id} must freeze exactly the five supervisor 1+4 identities")
        if not isinstance(topology, dict):
            raise ValueError(f"generation-v4 Run {run_id} is missing physical_topology")
        if (
            topology.get("worker_count") != 5
            or topology.get("leader") != "evaluation-manager"
            or topology.get("peer_mentions") is not False
            or "geo-policy-trend" in set(topology.get("workers", []))
        ):
            raise ValueError(f"generation-v4 Run {run_id} has an invalid physical topology")
    return generations


def _json(value: object) -> object:
    if isinstance(value, (UUID, datetime, date, Decimal)):
        return str(value)
    raise TypeError(f"unsupported acceptance value: {type(value).__name__}")


def _write(root: Path, name: str, value: object) -> None:
    (root / name).write_text(json.dumps(value, indent=2, sort_keys=True, default=_json) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--project-id", required=True, type=UUID)
    parser.add_argument("--run-id", required=True, action="append", type=UUID)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_ids = tuple(args.run_id)
    engine = create_database_engine(DatabaseSettings.from_env().url, application_role="launchscope_runtime")
    sessions = session_factory(engine)

    with tenant_transaction(sessions, TenantScope(args.tenant_id), actor_id="acceptance-export") as session:
        run_rows = session.execute(
            select(evaluation_run).where(
                evaluation_run.c.tenant_id == args.tenant_id,
                evaluation_run.c.project_id == args.project_id,
                evaluation_run.c.id.in_(run_ids),
            )
        ).mappings().all()
        if {row["id"] for row in run_rows} != set(run_ids):
            raise ValueError("every requested run must belong to the requested tenant and project")

        manifests = session.execute(
            select(run_manifest).where(run_manifest.c.tenant_id == args.tenant_id, run_manifest.c.run_id.in_(run_ids))
        ).mappings().all()
        if {row["run_id"] for row in manifests} != set(run_ids):
            raise ValueError("every requested run must have a frozen Run Manifest")
        product_version_ids = tuple(row["product_version_id"] for row in run_rows)
        brief_rows = session.execute(select(
            requirement_brief.c.id,
            requirement_brief.c.product_version_id,
            requirement_brief.c.revision,
            requirement_brief.c.schema_version,
            requirement_brief.c.raw_input_object_key,
            requirement_brief.c.raw_input_sha256,
            requirement_brief.c.confirmation_required,
            requirement_brief.c.status,
            requirement_brief.c.created_at,
            requirement_brief.c.confirmed_at,
        ).where(
            requirement_brief.c.tenant_id == args.tenant_id,
            requirement_brief.c.product_version_id.in_(product_version_ids),
        )).mappings().all()
        requirement_change_rows = session.execute(select(
            requirement_change.c.id,
            requirement_change.c.run_id,
            requirement_change.c.brief_id,
            requirement_change.c.status,
            requirement_change.c.created_at,
        ).where(
            requirement_change.c.tenant_id == args.tenant_id,
            requirement_change.c.run_id.in_(run_ids),
        )).mappings().all()
        plan_rows = session.execute(select(
            agent_plan.c.id,
            agent_plan.c.run_id,
            agent_plan.c.planning_task_id,
            agent_plan.c.dispatch_epoch,
            agent_plan.c.plan_version,
            agent_plan.c.evaluation_mode,
            agent_plan.c.plan_sha256,
            agent_plan.c.status,
            agent_plan.c.matrix_event_id,
            agent_plan.c.rejection_code,
            agent_plan.c.supersedes_plan_id,
            agent_plan.c.created_at,
            agent_plan.c.decided_at,
        ).where(agent_plan.c.tenant_id == args.tenant_id, agent_plan.c.run_id.in_(run_ids))).mappings().all()
        synthesis_rows = session.execute(select(
            manager_synthesis.c.id,
            manager_synthesis.c.run_id,
            manager_synthesis.c.task_id,
            manager_synthesis.c.dispatch_epoch,
            manager_synthesis.c.deterministic_candidate,
            manager_synthesis.c.proposed_recommendation,
            manager_synthesis.c.synthesis_sha256,
            manager_synthesis.c.status,
            manager_synthesis.c.approval_request_id,
            manager_synthesis.c.created_at,
        ).where(
            manager_synthesis.c.tenant_id == args.tenant_id,
            manager_synthesis.c.run_id.in_(run_ids),
        )).mappings().all()
        task_rows = session.execute(
            select(
                task.c.id,
                task.c.run_id,
                task.c.stage_code,
                task.c.agent_identity_ref,
                task.c.skill_ref,
                task.c.skill_version,
                task.c.status,
                task.c.dependencies,
                task.c.tool_allowlist,
                task.c.budget_slice,
                task.c.required,
            ).where(task.c.tenant_id == args.tenant_id, task.c.run_id.in_(run_ids))
        ).mappings().all()
        task_ids = tuple(row["id"] for row in task_rows)
        ticket_rows = session.execute(select(
            agent_task_ticket.c.id,
            agent_task_ticket.c.run_id,
            agent_task_ticket.c.task_id,
            agent_task_ticket.c.plan_id,
            agent_task_ticket.c.dispatch_epoch,
            agent_task_ticket.c.target_agent,
            agent_task_ticket.c.ticket_sha256,
            agent_task_ticket.c.status,
            agent_task_ticket.c.expires_at,
            agent_task_ticket.c.created_at,
            agent_task_ticket.c.delivered_at,
        ).where(
            agent_task_ticket.c.tenant_id == args.tenant_id,
            agent_task_ticket.c.run_id.in_(run_ids),
        )).mappings().all()
        skill_rows = session.execute(
            select(skill_invocation).where(
                skill_invocation.c.tenant_id == args.tenant_id, skill_invocation.c.task_id.in_(task_ids)
            )
        ).mappings().all() if task_ids else []
        skill_ids = tuple(row["id"] for row in skill_rows)
        tool_rows = session.execute(
            select(
                tool_invocation.c.id,
                tool_invocation.c.skill_invocation_id,
                tool_invocation.c.tool_code,
                tool_invocation.c.risk_tier,
                tool_invocation.c.status,
                tool_invocation.c.parameters_sha256,
                tool_invocation.c.created_at,
            ).where(
                tool_invocation.c.tenant_id == args.tenant_id,
                tool_invocation.c.skill_invocation_id.in_(skill_ids),
            )
        ).mappings().all() if skill_ids else []
        evidence_rows = session.execute(
            select(
                evidence.c.id,
                evidence.c.run_id,
                evidence.c.task_id,
                evidence.c.source_type,
                evidence.c.object_key,
                evidence.c.sha256,
                evidence.c.size_bytes,
                evidence.c.mime_type,
                evidence.c.evidence_level,
                evidence.c.trust_level,
                evidence.c.simulated,
                evidence.c.created_at,
            ).where(evidence.c.tenant_id == args.tenant_id, evidence.c.run_id.in_(run_ids))
        ).mappings().all()
        finding_rows = session.execute(
            select(
                finding.c.id,
                finding.c.run_id,
                finding.c.dimension_code,
                finding.c.grade,
                finding.c.claim_type,
                finding.c.is_hypothesis,
                finding.c.simulated,
                finding.c.hard_block,
            ).where(finding.c.tenant_id == args.tenant_id, finding.c.run_id.in_(run_ids))
        ).mappings().all()
        audit_rows = session.execute(
            select(
                evidence_audit.c.id,
                evidence_audit.c.run_id,
                evidence_audit.c.finding_id,
                evidence_audit.c.decision,
                evidence_audit.c.auditor_id,
                evidence_audit.c.reason,
                evidence_audit.c.audited_at,
            ).where(evidence_audit.c.tenant_id == args.tenant_id, evidence_audit.c.run_id.in_(run_ids))
        ).mappings().all()
        decision_rows = session.execute(
            select(
                decision.c.id,
                decision.c.run_id,
                decision.c.recommendation,
                decision.c.standard_version,
                decision.c.dimension_grades,
                decision.c.hard_blocks,
                decision.c.created_at,
            ).where(decision.c.tenant_id == args.tenant_id, decision.c.run_id.in_(run_ids))
        ).mappings().all()
        report_rows = session.execute(
            select(
                report.c.id,
                report.c.run_id,
                report.c.decision_id,
                report.c.object_key,
                report.c.sha256,
                report.c.status,
                report.c.created_at,
            ).where(report.c.tenant_id == args.tenant_id, report.c.run_id.in_(run_ids))
        ).mappings().all()
        dossier_rows = session.execute(select(
            project_dossier_snapshot.c.id,
            project_dossier_snapshot.c.project_id,
            project_dossier_snapshot.c.product_version_id,
            project_dossier_snapshot.c.run_id,
            project_dossier_snapshot.c.decision_id,
            project_dossier_snapshot.c.report_id,
            project_dossier_snapshot.c.schema_version,
            project_dossier_snapshot.c.sha256,
            project_dossier_snapshot.c.created_at,
        ).where(
            project_dossier_snapshot.c.tenant_id == args.tenant_id,
            project_dossier_snapshot.c.run_id.in_(run_ids),
        )).mappings().all()
        status_rows = session.execute(
            select(
                run_status_history.c.id,
                run_status_history.c.run_id,
                run_status_history.c.from_status,
                run_status_history.c.to_status,
                run_status_history.c.reason,
                run_status_history.c.failure_class,
                run_status_history.c.occurred_at,
            ).where(run_status_history.c.tenant_id == args.tenant_id, run_status_history.c.run_id.in_(run_ids))
        ).mappings().all()
        handoff_rows = session.execute(
            select(matrix_handoff).where(
                matrix_handoff.c.tenant_id == args.tenant_id, matrix_handoff.c.run_id.in_(run_ids)
            )
        ).mappings().all()
        binding_rows = session.execute(
            select(agentteams_run_binding).where(
                agentteams_run_binding.c.tenant_id == args.tenant_id,
                agentteams_run_binding.c.run_id.in_(run_ids),
            )
        ).mappings().all()
        delivery_rows = session.execute(select(
            agentteams_task_delivery.c.id,
            agentteams_task_delivery.c.run_id,
            agentteams_task_delivery.c.task_id,
            agentteams_task_delivery.c.dispatch_epoch,
            agentteams_task_delivery.c.agent_code,
            agentteams_task_delivery.c.room_id,
            agentteams_task_delivery.c.assignment_event_id,
            agentteams_task_delivery.c.status,
            agentteams_task_delivery.c.delivered_at,
            agentteams_task_delivery.c.deadline_at,
            agentteams_task_delivery.c.completed_at,
        ).where(
            agentteams_task_delivery.c.tenant_id == args.tenant_id,
            agentteams_task_delivery.c.run_id.in_(run_ids),
        )).mappings().all()
        matrix_receipts = session.execute(
            select(matrix_event_receipt).where(
                matrix_event_receipt.c.tenant_id == args.tenant_id,
                matrix_event_receipt.c.run_id.in_(run_ids),
            )
        ).mappings().all()
        approval_rows = session.execute(select(
            approval_request.c.id,
            approval_request.c.run_id,
            approval_request.c.tool_code,
            approval_request.c.parameters_sha256,
            approval_request.c.status,
            approval_request.c.expires_at,
            approval_request.c.created_at,
        ).where(
            approval_request.c.tenant_id == args.tenant_id,
            approval_request.c.run_id.in_(run_ids),
        )).mappings().all()
        usage_rows = session.execute(select(
            usage_record.c.id,
            usage_record.c.run_id,
            usage_record.c.task_id,
            usage_record.c.category,
            usage_record.c.quantity,
            usage_record.c.cost,
            usage_record.c.idempotency_key,
            usage_record.c.created_at,
        ).where(
            usage_record.c.tenant_id == args.tenant_id,
            usage_record.c.run_id.in_(run_ids),
        )).mappings().all()
        information_request_rows = session.execute(select(
            information_request.c.id,
            information_request.c.run_id,
            information_request.c.task_id,
            information_request.c.agent_identity_ref,
            information_request.c.profile_field,
            information_request.c.impact_dimension,
            information_request.c.answer_kind,
            information_request.c.status,
            information_request.c.answered_at,
            information_request.c.created_at,
            information_request.c.updated_at,
        ).where(
            information_request.c.tenant_id == args.tenant_id,
            information_request.c.run_id.in_(run_ids),
        )).mappings().all()
        information_request_ids = tuple(row["id"] for row in information_request_rows)
        information_answer_rows = session.execute(select(
            information_request_answer.c.id,
            information_request_answer.c.information_request_id,
            information_request_answer.c.run_id,
            information_request_answer.c.answer_sha256,
            information_request_answer.c.profile_revision,
            information_request_answer.c.evidence_id,
            information_request_answer.c.supersedes_id,
            information_request_answer.c.submission_sha256,
            information_request_answer.c.created_at,
        ).where(
            information_request_answer.c.tenant_id == args.tenant_id,
            information_request_answer.c.information_request_id.in_(information_request_ids),
        )).mappings().all() if information_request_ids else []
        clarification_rows = session.execute(select(
            clarification_impact_assessment.c.id,
            clarification_impact_assessment.c.run_id,
            clarification_impact_assessment.c.assessed_by_agent_ref,
            clarification_impact_assessment.c.answered_request_ids,
            clarification_impact_assessment.c.affected_task_ids,
            clarification_impact_assessment.c.unaffected_task_ids,
            clarification_impact_assessment.c.created_at,
        ).where(
            clarification_impact_assessment.c.tenant_id == args.tenant_id,
            clarification_impact_assessment.c.run_id.in_(run_ids),
        )).mappings().all()
        budget_rows = session.execute(
            select(budget_reservation).where(
                budget_reservation.c.tenant_id == args.tenant_id, budget_reservation.c.run_id.in_(run_ids)
            )
        ).mappings().all()
        trace_rows = session.execute(
            select(
                trace_metadata.c.run_id,
                trace_metadata.c.stage_id,
                trace_metadata.c.task_id,
                trace_metadata.c.correlation_id,
                trace_metadata.c.span_id,
                trace_metadata.c.payload_sha256,
                trace_metadata.c.created_at,
            ).where(trace_metadata.c.tenant_id == args.tenant_id, trace_metadata.c.run_id.in_(run_ids))
        ).mappings().all()
        outbox_rows = session.execute(
            select(
                outbox_message.c.id,
                outbox_message.c.event_id,
                outbox_message.c.aggregate_id,
                outbox_message.c.event_type,
                outbox_message.c.schema_version,
                outbox_message.c.publish_status,
                outbox_message.c.attempts,
                outbox_message.c.occurred_at,
            ).where(outbox_message.c.tenant_id == args.tenant_id, outbox_message.c.aggregate_id.in_(run_ids))
        ).mappings().all()
        outbox_ids = tuple(row["id"] for row in outbox_rows)
        inbox_rows = session.execute(select(
            inbox_message.c.id,
            inbox_message.c.outbox_message_id,
            inbox_message.c.consumer_name,
            inbox_message.c.dedupe_key,
            inbox_message.c.event_id,
            inbox_message.c.event_type,
            inbox_message.c.processing_status,
            inbox_message.c.received_at,
            inbox_message.c.processed_at,
            inbox_message.c.created_at,
        ).where(
            inbox_message.c.tenant_id == args.tenant_id,
            inbox_message.c.outbox_message_id.in_(outbox_ids),
        )).mappings().all() if outbox_ids else []
        delivery_attempt_rows = session.execute(select(
            event_delivery_attempt.c.id,
            event_delivery_attempt.c.outbox_message_id,
            event_delivery_attempt.c.attempt_no,
            event_delivery_attempt.c.status,
            event_delivery_attempt.c.attempted_at,
        ).where(
            event_delivery_attempt.c.tenant_id == args.tenant_id,
            event_delivery_attempt.c.outbox_message_id.in_(outbox_ids),
        )).mappings().all() if outbox_ids else []
        audit_event_rows = session.execute(
            select(
                audit_event.c.id,
                audit_event.c.run_id,
                audit_event.c.actor_type,
                audit_event.c.action,
                audit_event.c.outcome,
                audit_event.c.payload_sha256,
                audit_event.c.occurred_at,
            ).where(audit_event.c.tenant_id == args.tenant_id, audit_event.c.run_id.in_(run_ids))
        ).mappings().all()
        identities = session.execute(
            select(
                agent_identity.c.code,
                agent_identity.c.version,
                agent_identity.c.capabilities,
                agent_identity.c.allowed_actions,
            ).order_by(agent_identity.c.code)
        ).mappings().all()

    manifest_export = [dict(row) for row in manifests]
    generations = _validate_manifest_topology(manifest_export)
    for row in task_rows:
        if generations[str(row["run_id"])] == "v4" and row["agent_identity_ref"].split("@", 1)[0] == "geo-policy-trend":
            raise ValueError(f"generation-v4 Run {row['run_id']} contains a forbidden geo-policy-trend Task")
    _write(root, "run-manifests.json", manifest_export)
    _write(root, "requirement-brief-index.json", [dict(row) for row in brief_rows])
    _write(root, "requirement-change-index.json", [dict(row) for row in requirement_change_rows])
    _write(root, "manager-plan-index.json", [dict(row) for row in plan_rows])
    _write(root, "agent-task-ticket-index.json", [dict(row) for row in ticket_rows])
    _write(root, "manager-synthesis-index.json", [dict(row) for row in synthesis_rows])
    identity_export: list[dict[str, object]] = [dict(row) for row in identities]
    if not identity_export:
        frozen_agents: dict[str, dict[str, object]] = {}
        for manifest_row in manifests:
            agents = manifest_row["frozen_config"].get("agents", {})
            for code, contract in agents.items():
                frozen_agents[code] = {"code": code, **contract, "source": "run_manifest.frozen_config"}
        identity_export = [frozen_agents[code] for code in sorted(frozen_agents)]
    _write(root, "agent-identities.json", identity_export)
    _write(root, "task-dag.json", [dict(row) for row in task_rows])
    _write(root, "matrix-handoffs.json", [dict(row) for row in handoff_rows])
    _write(root, "agentteams-bindings.json", [dict(row) for row in binding_rows])
    _write(root, "agentteams-task-deliveries.json", [dict(row) for row in delivery_rows])
    _write(root, "matrix-event-receipts.json", [dict(row) for row in matrix_receipts])
    _write(root, "tool-invocations.json", [dict(row) for row in tool_rows])
    _write(root, "evidence-index.json", [dict(row) for row in evidence_rows])
    _write(
        root,
        "finding-audits.json",
        {"findings": [dict(row) for row in finding_rows], "audits": [dict(row) for row in audit_rows]},
    )
    _write(
        root,
        "report-index.json",
        {"decisions": [dict(row) for row in decision_rows], "reports": [dict(row) for row in report_rows]},
    )
    _write(root, "sse-status-history.json", [dict(row) for row in status_rows])
    _write(root, "outbox-index.json", [dict(row) for row in outbox_rows])
    _write(root, "inbox-index.json", [dict(row) for row in inbox_rows])
    _write(root, "event-delivery-attempt-index.json", [dict(row) for row in delivery_attempt_rows])
    _write(root, "audit-index.json", [dict(row) for row in audit_event_rows])
    _write(root, "budget-index.json", [dict(row) for row in budget_rows])
    _write(root, "usage-index.json", [dict(row) for row in usage_rows])
    _write(root, "approval-index.json", [dict(row) for row in approval_rows])
    _write(
        root,
        "clarification-index.json",
        {
            "requests": [dict(row) for row in information_request_rows],
            "answers": [dict(row) for row in information_answer_rows],
            "impact_assessments": [dict(row) for row in clarification_rows],
        },
    )
    _write(root, "project-dossier-index.json", [dict(row) for row in dossier_rows])
    _write(
        root,
        "trace-summary.json",
        {
            "trace_metadata": [dict(row) for row in trace_rows],
            "trace_metadata_count": len(trace_rows),
            "durable_status_event_count": len(status_rows),
            "structured_handoff_count": len(handoff_rows),
            "note": (
                "Durable status and handoff facts remain the replay source when no external OTel backend is configured."
            ),
        },
    )
    _write(
        root,
        "version-comparison.json",
        {
            "project_id": args.project_id,
            "runs": [dict(row) for row in sorted(run_rows, key=lambda row: row["created_at"])],
            "same_standard": len({row["standard_version"] for row in run_rows}) == 1,
        },
    )
    execution_modes = {row["frozen_config"].get("execution_mode", "UNKNOWN") for row in manifests}
    run_claim_components: dict[str, dict[str, bool]] = {}
    for run_id in run_ids:
        run_key = str(run_id)
        run_row = next(row for row in run_rows if row["id"] == run_id)
        run_outbox = [row for row in outbox_rows if row["aggregate_id"] == run_id]
        run_outbox_ids = {row["id"] for row in run_outbox}
        run_task_ids = {row["id"] for row in task_rows if row["run_id"] == run_id}
        run_skill_ids = {row["id"] for row in skill_rows if row["task_id"] in run_task_ids}
        run_components = {
            "completed": run_row["status"] == "COMPLETED" and not run_row["last_failure_class"],
            "live_execution_mode": any(
                row["run_id"] == run_id
                and row["frozen_config"].get("execution_mode") == "AGENTTEAMS_V1_2_ROCKETMQ"
                for row in manifests
            ),
            "agentteams_binding": any(row["run_id"] == run_id for row in binding_rows),
            "task_deliveries": any(row["run_id"] == run_id for row in delivery_rows),
            "matrix_receipts": any(row["run_id"] == run_id for row in matrix_receipts),
            "outbox_published": bool(run_outbox)
            and all(row["publish_status"] == "PUBLISHED" for row in run_outbox),
            "inbox_receipts": any(row["outbox_message_id"] in run_outbox_ids for row in inbox_rows),
            "tool_and_evidence": any(row["skill_invocation_id"] in run_skill_ids for row in tool_rows)
            and any(row["run_id"] == run_id for row in evidence_rows),
            "usage_known": any(row["run_id"] == run_id for row in usage_rows),
            "budget_known": any(row["run_id"] == run_id for row in budget_rows),
            "decision_report_committed": any(row["run_id"] == run_id for row in decision_rows)
            and any(row["run_id"] == run_id and row["status"] == "COMMITTED" for row in report_rows),
            "v4_plan_synthesis_dossier": generations[run_key] != "v4"
            or (
                any(row["run_id"] == run_id for row in plan_rows)
                and any(row["run_id"] == run_id for row in synthesis_rows)
                and any(row["run_id"] == run_id for row in dossier_rows)
            ),
        }
        run_claim_components[run_key] = run_components
    external_e2e_claim = bool(run_claim_components) and all(
        all(components.values()) for components in run_claim_components.values()
    )
    _write(
        root,
        "snapshot-metadata.json",
        {
            "label": (
                "Sanitized live acceptance bundle"
                if external_e2e_claim
                else "Recorded or partial acceptance bundle"
            ),
            "generated_at": datetime.now().astimezone(),
            "read_only": True,
            "run_ids": run_ids,
            "manifest_generations": generations,
            "execution_modes": sorted(execution_modes),
            "run_claim_components": run_claim_components,
            "external_e2e_claim": external_e2e_claim,
            "provider_model_identity_note": (
                "Declared and effective Worker models require separately captured runtime-config and provider receipt "
                "evidence."
            ),
            "redaction": "No material body, prompt, private reasoning, API key, or Matrix token is exported.",
        },
    )
    files = sorted(path for path in root.iterdir() if path.is_file() and path.name != "hashes.txt")
    (root / "hashes.txt").write_text(
        "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in files), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
