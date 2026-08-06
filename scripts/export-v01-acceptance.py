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
    agentteams_run_binding,
    audit_event,
    budget_reservation,
    decision,
    evaluation_run,
    evidence,
    evidence_audit,
    finding,
    matrix_event_receipt,
    matrix_handoff,
    outbox_message,
    report,
    run_manifest,
    run_status_history,
    skill_invocation,
    task,
    tool_invocation,
    trace_metadata,
)
from launchscope_api.infrastructure.db.session import (
    DatabaseSettings,
    create_database_engine,
    session_factory,
    tenant_transaction,
)
from launchscope_domain.value_objects import TenantScope


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
        matrix_receipts = session.execute(
            select(matrix_event_receipt).where(
                matrix_event_receipt.c.tenant_id == args.tenant_id,
                matrix_event_receipt.c.run_id.in_(run_ids),
            )
        ).mappings().all()
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
                outbox_message.c.event_id,
                outbox_message.c.aggregate_id,
                outbox_message.c.event_type,
                outbox_message.c.schema_version,
                outbox_message.c.publish_status,
                outbox_message.c.attempts,
                outbox_message.c.occurred_at,
            ).where(outbox_message.c.tenant_id == args.tenant_id, outbox_message.c.aggregate_id.in_(run_ids))
        ).mappings().all()
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

    _write(root, "run-manifests.json", [dict(row) for row in manifests])
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
    _write(root, "audit-index.json", [dict(row) for row in audit_event_rows])
    _write(root, "budget-index.json", [dict(row) for row in budget_rows])
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
    execution_modes = {
        row["frozen_config"].get("execution_mode", "UNKNOWN") for row in manifests
    }
    _write(
        root,
        "snapshot-metadata.json",
        {
            "label": "Recorded acceptance snapshot",
            "generated_at": datetime.now().astimezone(),
            "read_only": True,
            "run_ids": run_ids,
            "execution_modes": sorted(execution_modes),
            "external_e2e_claim": (
                "AGENTTEAMS_V1_2_ROCKETMQ" in execution_modes
                and bool(binding_rows)
                and bool(matrix_receipts)
                and bool(outbox_rows)
                and all(row["publish_status"] == "PUBLISHED" for row in outbox_rows)
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
