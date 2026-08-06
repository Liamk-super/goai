"""Deterministic Local/Demo vertical slice over real PostgreSQL and private S3.

This coordinator deliberately does not claim an external model, AgentTeams
service, Matrix server, browser or public-research call. It exercises the same
versioned identities/contracts, a real zero-cost repository sandbox tool, and
the durable control-plane boundaries needed for a reproducible offline demo.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TypedDict, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    audit_event,
    budget_reservation,
    decision,
    decision_finding,
    evaluation_run,
    evidence,
    evidence_audit,
    finding,
    finding_evidence,
    material,
    matrix_handoff,
    outbox_message,
    project,
    report,
    run_manifest,
    run_status_history,
    skill_invocation,
    skill_version,
    stage,
    task,
    tool_invocation,
    workspace_member,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.infrastructure.object_store import S3QuarantineObjectStore
from launchscope_api.modules.identity_tenant.application import Actor, AuthorizationError, NotFoundError
from launchscope_domain.enums import STAGE_ORDER
from launchscope_domain.value_objects import TenantScope
from launchscope_orchestrator.agentteams_adapter import AgentTeamsAdapter
from launchscope_orchestrator.manifest_loader import AgentManifestLoader
from launchscope_skills import SkillRegistry
from launchscope_worker.tool_gateway.contract import ToolContractRegistry
from launchscope_worker.tools.repository_read import RepositoryReader


@dataclass(frozen=True, slots=True)
class VerticalSliceResult:
    run_id: UUID
    report_id: UUID
    status: str
    manifest_sha256: str
    evidence_ids: tuple[UUID, ...]
    handoff_count: int
    tool_invocation_count: int
    execution_mode: str = "LOCAL_DETERMINISTIC_READONLY"


FindingSpec = tuple[str, str, str, bool]


class _VerticalSliceSnapshot(TypedDict):
    project_id: UUID
    version_id: UUID
    status: str
    materials: list[dict[str, str]]
    report_id: UUID | None
    manifest_sha256: str
    evidence_ids: tuple[UUID, ...]
    handoff_count: int
    tool_invocation_count: int


class VerticalSliceApplication:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        objects: S3QuarantineObjectStore,
        fixture_root: Path,
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._fixture_root = fixture_root.resolve(strict=True)

    def execute(self, actor: Actor, run_id: UUID, *, fixture_path: str) -> VerticalSliceResult:
        snapshot = self._load(actor, run_id)
        existing_report_id = snapshot.get("report_id")
        if existing_report_id is not None:
            return VerticalSliceResult(
                run_id,
                existing_report_id,
                "COMPLETED",
                snapshot["manifest_sha256"],
                tuple(snapshot["evidence_ids"]),
                snapshot["handoff_count"],
                snapshot["tool_invocation_count"],
            )

        contracts = AgentManifestLoader().load_all()
        team = AgentTeamsAdapter().create_team(run_id, contracts)
        skill_hashes = {item.skill_code: item.content_sha256 for item in SkillRegistry().load_p0()}
        tool_contract = ToolContractRegistry().load("repository.read.v1")
        tool_result = RepositoryReader(self._fixture_root).read(
            {"path": fixture_path, "max_bytes": 262144}, tool_contract
        )
        source_bytes = json.dumps(tool_result.evidence, sort_keys=True, separators=(",", ":")).encode()
        evidence_id = uuid5(NAMESPACE_URL, f"launchscope.demo.evidence:{run_id}:repository")
        evidence_key = (
            f"tenant/{actor.tenant_id}/project/{snapshot['project_id']}/version/{snapshot['version_id']}/"
            f"run/{run_id}/evidence/{evidence_id}/repository.json"
        )
        evidence_digest = self._objects.put_private(evidence_key, source_bytes, "application/json")

        manifest = {
            "schema_version": "1.0",
            "execution_mode": "LOCAL_DETERMINISTIC_READONLY",
            "product_version_id": str(snapshot["version_id"]),
            "materials": snapshot["materials"],
            "standard_version": "1.0",
            "agents": {item.code: {"version": item.version, "sha256": item.content_sha256} for item in contracts},
            "skills": skill_hashes,
            "tools": {"repository.read.v1": tool_contract.version},
            "models": {"deterministic-local-rules": "1.0"},
            "prompts": {"local-evidence-synthesis": "1.0"},
            "permissions": ["repository.read"],
            "failure_policy": {"SUBMISSION_UNKNOWN": "NEEDS_ATTENTION_NO_RETRY"},
            "budget": {"token": "0", "tool": "0", "external_cost": "0"},
        }
        manifest_sha = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        report_id = uuid5(NAMESPACE_URL, f"launchscope.demo.report:{run_id}")
        report_body = self._report_html(run_id, evidence_id, evidence_digest)
        report_key = (
            f"tenant/{actor.tenant_id}/project/{snapshot['project_id']}/version/{snapshot['version_id']}/"
            f"run/{run_id}/reports/{report_id}.html"
        )
        report_digest = self._objects.put_private(report_key, report_body.encode(), "text/html")
        finding_specs: tuple[FindingSpec, ...] = (
            ("product-engineering", "PRODUCT_IMPLEMENTATION", "MODERATE", False),
            ("user-evidence", "USER_USAGE", "INSUFFICIENT_EVIDENCE", True),
            ("business-investment", "BUSINESS_INVESTMENT", "WEAK", False),
            ("geo-policy-trend", "GEO_POLICY_TREND", "INSUFFICIENT_EVIDENCE", True),
        )
        finding_ids = tuple(
            uuid5(NAMESPACE_URL, f"launchscope.demo.finding:{run_id}:{code}") for code, *_ in finding_specs
        )
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            current = session.execute(
                select(evaluation_run.c.status)
                .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                .with_for_update()
            ).scalar_one()
            if current != "PLANNED":
                raise ValueError("local vertical slice requires a durable PLANNED run")
            session.execute(
                run_manifest.insert().values(
                    run_id=run_id,
                    tenant_id=actor.tenant_id,
                    frozen_config=manifest,
                    manifest_sha256=manifest_sha,
                    budget=manifest["budget"],
                    security_policy={"permissions": ["repository.read"], "version": "1.0"},
                    created_at=now,
                )
            )
            for category in ("token", "tool", "external_cost"):
                session.execute(
                    budget_reservation.insert().values(
                        id=uuid4(),
                        tenant_id=actor.tenant_id,
                        run_id=run_id,
                        category=category,
                        currency="USD",
                        limit_amount=Decimal("0"),
                        reserved_amount=Decimal("0"),
                        consumed_amount=Decimal("0"),
                        released_amount=Decimal("0"),
                        status="RESERVED",
                        idempotency_key=f"demo:{run_id}:budget:{category}",
                        created_at=now,
                        updated_at=now,
                    )
                )
            task_ids = self._persist_execution_graph(session, actor.tenant_id, run_id, finding_specs, now)
            tool_id = uuid5(NAMESPACE_URL, f"launchscope.demo.tool:{run_id}")
            invocation_id = uuid5(NAMESPACE_URL, f"launchscope.demo.skill:{run_id}")
            browser_skill_id = session.execute(
                select(skill_version.c.id).where(
                    skill_version.c.skill_code == "browser-product-audit",
                    skill_version.c.version == "1.0",
                )
            ).scalar_one()
            session.execute(
                skill_invocation.insert().values(
                    id=invocation_id,
                    tenant_id=actor.tenant_id,
                    task_id=task_ids[0],
                    skill_version_id=browser_skill_id,
                    status="SUCCEEDED",
                    idempotency_key=f"demo:{run_id}:skill",
                    estimated_cost=0,
                    created_at=now,
                )
            )
            session.execute(
                tool_invocation.insert().values(
                    id=tool_id,
                    tenant_id=actor.tenant_id,
                    skill_invocation_id=invocation_id,
                    tool_code="repository.read.v1",
                    risk_tier="LOW",
                    status="SUCCEEDED",
                    parameters_sha256=hashlib.sha256(
                        json.dumps({"path": fixture_path}, sort_keys=True).encode()
                    ).hexdigest(),
                    created_at=now,
                )
            )
            session.execute(
                evidence.insert().values(
                    id=evidence_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    task_id=task_ids[0],
                    material_id=None,
                    source_type="REPOSITORY",
                    object_key=evidence_key,
                    sha256=evidence_digest,
                    size_bytes=len(source_bytes),
                    mime_type="application/json",
                    evidence_level="E3",
                    trust_level="E3",
                    summary="Read-only repository artifact hash captured by the sandboxed Tool Contract.",
                    fetched_at=now,
                    valid_until=now + timedelta(days=90),
                    region="LOCAL",
                    simulated=False,
                    created_at=now,
                )
            )
            for index, ((agent_code, dimension, grade, degraded), finding_id) in enumerate(
                zip(finding_specs, finding_ids, strict=True)
            ):
                session.execute(
                    finding.insert().values(
                        id=finding_id,
                        tenant_id=actor.tenant_id,
                        run_id=run_id,
                        task_id=task_ids[index],
                        dimension_code=dimension,
                        grade=grade,
                        claim_type="FINDING" if not degraded else "HYPOTHESIS",
                        statement=self._finding_statement(dimension, degraded),
                        is_hypothesis=degraded,
                        submitted_by=agent_code,
                        submitted_at=now,
                        structured_result={"source": "local-deterministic-readonly"},
                        simulated=False,
                        hard_block=False,
                    )
                )
                session.execute(
                    finding_evidence.insert().values(
                        tenant_id=actor.tenant_id,
                        finding_id=finding_id,
                        evidence_id=evidence_id,
                        relation_type="SUPPORTS",
                    )
                )
                audit_decision = "DOWNGRADED" if degraded else "ACCEPTED"
                session.execute(
                    evidence_audit.insert().values(
                        id=uuid4(),
                        tenant_id=actor.tenant_id,
                        run_id=run_id,
                        finding_id=finding_id,
                        decision=audit_decision,
                        auditor_id="evidence-auditor",
                        reason="local fixture cannot establish external demand"
                        if degraded
                        else "hash and scope verified",
                        audited_at=now,
                    )
                )
                handoff_payload = {
                    "finding_id": str(finding_id),
                    "evidence_ids": [str(evidence_id)],
                    "decision": audit_decision,
                }
                session.execute(
                    matrix_handoff.insert().values(
                        id=uuid4(),
                        tenant_id=actor.tenant_id,
                        run_id=run_id,
                        task_id=task_ids[index],
                        room_id=f"run:{run_id}:task:{task_ids[index]}:matrix",
                        sender_agent=agent_code,
                        receiver_agent=team.manager.code,
                        kind="FINDING",
                        finding_id=finding_id,
                        evidence_ids=[str(evidence_id)],
                        risk="LOW",
                        confidence=Decimal("0.7000") if not degraded else Decimal("0.3500"),
                        approval_required=False,
                        payload_sha256=hashlib.sha256(json.dumps(handoff_payload, sort_keys=True).encode()).hexdigest(),
                        created_at=now,
                    )
                )
            decision_id = uuid5(NAMESPACE_URL, f"launchscope.demo.decision:{run_id}")
            grades = {dimension: grade for _, dimension, grade, _ in finding_specs}
            session.execute(
                decision.insert().values(
                    id=decision_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    recommendation="VALIDATE_FURTHER",
                    standard_version="1.0",
                    dimension_grades=grades,
                    hard_blocks=[],
                    created_at=now,
                )
            )
            for finding_id in finding_ids:
                session.execute(
                    decision_finding.insert().values(
                        tenant_id=actor.tenant_id, decision_id=decision_id, finding_id=finding_id, role="SUPPORTING"
                    )
                )
            session.execute(
                report.insert().values(
                    id=report_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    decision_id=decision_id,
                    object_key=report_key,
                    sha256=report_digest,
                    status="COMMITTED",
                    action_items=[
                        "Run one authorized external read-only research case",
                        "Collect real user evidence",
                        "Repeat V2 with the same standard",
                    ],
                    created_at=now,
                )
            )
            self._persist_status_and_events(session, actor.tenant_id, run_id, snapshot["project_id"], now)
            self._audit(
                session,
                actor.tenant_id,
                run_id,
                "vertical_slice.completed",
                {"mode": "LOCAL_DETERMINISTIC_READONLY", "manifest_sha256": manifest_sha},
                now,
            )
        return VerticalSliceResult(run_id, report_id, "COMPLETED", manifest_sha, (evidence_id,), 4, 1)

    def _load(self, actor: Actor, run_id: UUID) -> _VerticalSliceSnapshot:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            row = (
                session.execute(
                    select(
                        evaluation_run.c.project_id, evaluation_run.c.product_version_id, evaluation_run.c.status
                    ).where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFoundError("run was not found")
            allowed = session.execute(
                select(workspace_member.c.id)
                .select_from(
                    project.join(
                        workspace_member,
                        (workspace_member.c.tenant_id == project.c.tenant_id)
                        & (workspace_member.c.workspace_id == project.c.workspace_id),
                    )
                )
                .where(
                    project.c.tenant_id == actor.tenant_id,
                    project.c.id == row["project_id"],
                    workspace_member.c.actor_id == actor.actor_id,
                )
            ).first()
            if allowed is None:
                raise AuthorizationError("workspace membership is required")
            existing = session.execute(
                select(report.c.id).where(report.c.tenant_id == actor.tenant_id, report.c.run_id == run_id)
            ).scalar_one_or_none()
            manifest_row = session.execute(
                select(run_manifest.c.manifest_sha256).where(
                    run_manifest.c.tenant_id == actor.tenant_id, run_manifest.c.run_id == run_id
                )
            ).scalar_one_or_none()
            evidence_ids = tuple(
                session.execute(
                    select(evidence.c.id).where(evidence.c.tenant_id == actor.tenant_id, evidence.c.run_id == run_id)
                ).scalars()
            )
            materials = [
                {"id": str(item.id), "sha256": item.sha256}
                for item in session.execute(
                    select(material.c.id, material.c.sha256).where(
                        material.c.tenant_id == actor.tenant_id,
                        material.c.product_version_id == row["product_version_id"],
                        material.c.ingest_status == "VALIDATED",
                    )
                ).all()
            ]
            if not materials:
                raise ValueError("at least one validated material is required")
            return {
                "project_id": cast(UUID, row["project_id"]),
                "version_id": cast(UUID, row["product_version_id"]),
                "status": cast(str, row["status"]),
                "materials": materials,
                "report_id": cast(UUID | None, existing),
                "manifest_sha256": manifest_row or "",
                "evidence_ids": cast(tuple[UUID, ...], evidence_ids),
                "handoff_count": 4 if existing else 0,
                "tool_invocation_count": 1 if existing else 0,
            }

    @staticmethod
    def _persist_execution_graph(
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        finding_specs: tuple[FindingSpec, ...],
        now: datetime,
    ) -> tuple[UUID, ...]:
        for ordinal, code in enumerate(STAGE_ORDER, start=1):
            session.execute(
                stage.insert().values(
                    id=uuid5(NAMESPACE_URL, f"launchscope.demo.stage:{run_id}:{code.value}"),
                    tenant_id=tenant_id,
                    run_id=run_id,
                    code=code.value,
                    ordinal=ordinal,
                    status="COMPLETED",
                    started_at=now,
                    completed_at=now,
                )
            )
        task_ids = []
        for agent_code, *_ in finding_specs:
            task_id = uuid5(NAMESPACE_URL, f"launchscope.demo.task:{run_id}:{agent_code}")
            task_ids.append(task_id)
            session.execute(
                task.insert().values(
                    id=task_id,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    stage_id=uuid5(NAMESPACE_URL, f"launchscope.demo.stage:{run_id}:PARALLEL_EVALUATION"),
                    agent_identity_id=None,
                    skill_version_id=None,
                    stage_code="PARALLEL_EVALUATION",
                    agent_identity_ref=f"{agent_code}@1.0",
                    skill_ref="browser-product-audit"
                    if agent_code in {"product-engineering", "user-evidence"}
                    else "business-investment-assessment",
                    skill_version="1.0",
                    status="SUCCEEDED",
                    idempotency_key=f"demo:{run_id}:task:{agent_code}",
                    dependencies=[],
                    tool_allowlist=["repository.read.v1"] if agent_code == "product-engineering" else [],
                    budget_slice={"category": "tool", "reserved": "0"},
                    timeout_seconds=30,
                    success_condition={"description": "evidence-backed structured finding"},
                    evidence_requirement="one immutable evidence ref",
                    required=True,
                    correction_attempts=0,
                    transient_retries=0,
                    side_effect_started=False,
                    created_at=now,
                    updated_at=now,
                )
            )
        auditor_id = uuid5(NAMESPACE_URL, f"launchscope.demo.task:{run_id}:evidence-auditor")
        task_ids.append(auditor_id)
        session.execute(
            task.insert().values(
                id=auditor_id,
                tenant_id=tenant_id,
                run_id=run_id,
                stage_id=uuid5(NAMESPACE_URL, f"launchscope.demo.stage:{run_id}:EVIDENCE_REVIEW"),
                agent_identity_id=None,
                skill_version_id=None,
                stage_code="EVIDENCE_REVIEW",
                agent_identity_ref="evidence-auditor@1.0",
                skill_ref="evidence-grounding-audit",
                skill_version="1.0",
                status="SUCCEEDED",
                idempotency_key=f"demo:{run_id}:task:evidence-auditor",
                dependencies=[str(value) for value in task_ids[:-1]],
                tool_allowlist=[],
                budget_slice={"category": "tool", "reserved": "0"},
                timeout_seconds=30,
                success_condition={"description": "all findings independently audited"},
                evidence_requirement="audit decision per finding",
                required=True,
                correction_attempts=0,
                transient_retries=0,
                side_effect_started=False,
                created_at=now,
                updated_at=now,
            )
        )
        return tuple(task_ids)

    @staticmethod
    def _persist_status_and_events(
        session: Session, tenant_id: UUID, run_id: UUID, project_id: UUID, now: datetime
    ) -> None:
        transitions = (
            ("PLANNED", "RUNNING", "manifest frozen and zero-cost budget reserved"),
            ("RUNNING", "EVIDENCE_REVIEW", "required tasks terminal"),
            ("EVIDENCE_REVIEW", "SYNTHESIZING", "audit accepted and downgraded unsupported claims"),
            ("SYNTHESIZING", "COMPLETED", "decision report dossier committed"),
        )
        for index, (before, after, reason) in enumerate(transitions):
            history_id = uuid5(NAMESPACE_URL, f"launchscope.demo.history:{run_id}:{index}")
            session.execute(
                run_status_history.insert().values(
                    id=history_id,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    from_status=before,
                    to_status=after,
                    reason=reason,
                    occurred_at=now + timedelta(milliseconds=index),
                )
            )
            payload = {
                "event_type": "run.status_changed",
                "schema_version": "1.0",
                "event_id": str(history_id),
                "tenant_id": str(tenant_id),
                "run_id": str(run_id),
                "task_id": None,
                "correlation_id": str(run_id),
                "causation_id": None,
                "idempotency_key": f"demo:{run_id}:status:{index}",
                "occurred_at": (now + timedelta(milliseconds=index)).isoformat(),
                "payload": {"project_id": str(project_id), "status": after, "reason": reason},
            }
            session.execute(
                outbox_message.insert().values(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    aggregate_id=run_id,
                    aggregate_type="evaluation_run",
                    event_type="run.status_changed",
                    event_id=history_id,
                    schema_version="1.0",
                    idempotency_key=f"demo:{run_id}:status:{index}",
                    payload=payload,
                    publish_status="PENDING",
                    available_at=now,
                    attempts=0,
                    occurred_at=now,
                    created_at=now,
                )
            )
        session.execute(
            update(evaluation_run)
            .where(evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id)
            .values(
                status="COMPLETED",
                current_stage="VERSION_REGRESSION",
                state_flags={
                    "gap_identified": True,
                    "profile_confirmed": True,
                    "material_profile_complete": True,
                    "budget_reserved": True,
                    "required_tasks_terminal": True,
                    "audit_ready": True,
                    "decision_committed": True,
                    "report_committed": True,
                    "dossier_committed": True,
                },
                updated_at=now,
            )
        )

    @staticmethod
    def _audit(
        session: Session, tenant_id: UUID, run_id: UUID, action: str, metadata: dict[str, str], now: datetime
    ) -> None:
        digest = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
        session.execute(
            audit_event.insert().values(
                id=uuid4(),
                tenant_id=tenant_id,
                run_id=run_id,
                actor_type="CONTROL_PLANE",
                action=action,
                outcome="SUCCESS",
                payload_sha256=digest,
                metadata=metadata,
                occurred_at=now,
            )
        )

    @staticmethod
    def _finding_statement(dimension: str, degraded: bool) -> str:
        return (
            f"{dimension}: local fixture evidence is insufficient for an external-world claim."
            if degraded
            else f"{dimension}: the authorized local artifact was read successfully through the sandbox contract."
        )

    @staticmethod
    def _report_html(run_id: UUID, evidence_id: UUID, digest: str) -> str:
        return (
            "<!doctype html><html><body><h1>LaunchScope local evidence report</h1>"
            f"<p>Run {run_id}</p><p>Evidence {evidence_id}</p><p>SHA-256 {digest}</p>"
            "<p>Recommendation: VALIDATE_FURTHER. External research is not claimed.</p>"
            "</body></html>"
        )


__all__ = ["VerticalSliceApplication", "VerticalSliceResult"]
