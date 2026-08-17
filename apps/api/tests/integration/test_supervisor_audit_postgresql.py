from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from launchscope_api.infrastructure.db.schema import (
    agent_report_artifact,
    agent_task_ticket,
    decision,
    evaluation_run,
    evidence,
    evidence_audit,
    finding,
    manager_synthesis,
    material,
    material_analysis,
    material_unit,
    project,
    project_dossier_snapshot,
    report,
    report_claim_citation,
    requirement_brief,
    run_manifest,
    stage,
    task,
    task_material_scope,
)
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.supervisor.audit_application import SupervisorAuditApplication, SupervisorWorkflowError
from launchscope_api.modules.supervisor.completion_application import (
    CompletionValidationError,
    SupervisorCompletionApplication,
)
from launchscope_api.modules.supervisor.planning_application import ManagerPlanningApplication


@dataclass(frozen=True)
class _Metadata:
    sha256: str


class _Objects:
    def __init__(self) -> None:
        self.values: dict[str, _Metadata] = {}
        self.bodies: dict[str, bytes] = {}
        self.fail_writes = False

    def add(self, key: str, digest: str) -> None:
        self.values[key] = _Metadata(digest)

    def head(self, key: str) -> _Metadata | None:
        return self.values.get(key)

    def put_private(self, key: str, body: bytes, _mime_type: str) -> str:
        if self.fail_writes:
            raise RuntimeError("object write outcome is unknown")
        digest = hashlib.sha256(body).hexdigest()
        self.values[key] = _Metadata(digest)
        self.bodies[key] = body
        return digest

    def get_private(self, key: str, *, max_bytes: int = 2_000_000) -> bytes:
        body = self.bodies[key]
        if len(body) > max_bytes:
            raise RuntimeError("object exceeds read limit")
        return body


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _seed_planning(database, records, mode: str = "FULL_POTENTIAL") -> tuple[UUID, UUID]:
    now = datetime.now(UTC)
    brief_id, stage_id, planning_task_id = uuid4(), uuid4(), uuid4()
    with database.begin() as connection:
        connection.execute(
            requirement_brief.insert().values(
                id=brief_id,
                tenant_id=records["tenant_id"],
                product_version_id=records["version_id"],
                revision=1,
                schema_version="1.0",
                raw_input_object_key=f"test/{brief_id}.txt",
                raw_input_sha256="a" * 64,
                document={
                    "schema_version": "1.0",
                    "brief_id": str(brief_id),
                    "evaluation_mode": mode,
                },
                confirmation_required=False,
                status="READY_FOR_PLANNING",
                created_by="integration",
                created_at=now,
                confirmed_at=now,
            )
        )
        connection.execute(
            stage.insert().values(
                id=stage_id,
                tenant_id=records["tenant_id"],
                run_id=records["run_id"],
                code="LEADER_PLANNING",
                ordinal=1,
                status="COMPLETED",
                started_at=now,
                completed_at=now,
            )
        )
        connection.execute(
            task.insert().values(
                id=planning_task_id,
                tenant_id=records["tenant_id"],
                run_id=records["run_id"],
                stage_id=stage_id,
                agent_identity_id=None,
                skill_version_id=None,
                stage_code="LEADER_PLANNING",
                agent_identity_ref="evaluation-manager@4.0",
                skill_ref="launchscope-evaluation-manager-handoff-v1",
                skill_version="4.0",
                status="SUCCEEDED",
                lease_token=None,
                idempotency_key=f"v4-planning-{planning_task_id}",
                dependencies=[],
                tool_allowlist=["launchscope-context.get.v1"],
                budget_slice={"suggested_usd": 0},
                timeout_seconds=600,
                success_condition=["valid ManagerPlanV1"],
                evidence_requirement=None,
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
        connection.execute(
            run_manifest.insert().values(
                run_id=records["run_id"],
                tenant_id=records["tenant_id"],
                manifest_sha256="b" * 64,
                frozen_config={"architecture_generation": "supervisor-1p4-v1", "agent_contract_generation": "v4"},
                budget={"currency": "USD", "limit": "20"},
                security_policy={},
                created_at=now,
            )
        )
    return brief_id, planning_task_id


def _plan(run_id: UUID, brief_id: UUID, mode: str = "FULL_POTENTIAL") -> dict[str, object]:
    agents = ("user-evidence", "product-engineering", "business-investment")
    external_tools = {
        "user-evidence": "public-research-search.v1",
        "product-engineering": "browser-audit.v1",
        "business-investment": "public-research-search.v1",
    }
    tasks = [
        {
            "task_key": agent,
            "target_agent": agent,
            "input_refs": ["requirement-brief:current"],
            "analysis_dimensions": [agent],
            "region_scope": ["Hong Kong"],
            "as_of": date.today().isoformat(),
            "tool_policy": ["launchscope-context.get.v1", external_tools[agent]],
            "success_conditions": ["traceable findings and a SHA-bound report"],
            "required": mode != "INVESTMENT_REVIEW" or agent == "business-investment",
            "dependencies": [],
            "budget_suggestion": 2,
            "deadline_seconds": 600,
        }
        for agent in agents
    ]
    return {
        "schema_version": "1.0",
        "plan_id": str(uuid4()),
        "run_id": str(run_id),
        "brief_id": str(brief_id),
        "plan_version": 1,
        "supersedes_plan_id": None,
        "evaluation_mode": mode,
        "score_profile_ref": (
            "score-profile:investment-review@1.0" if mode == "INVESTMENT_REVIEW" else "score-profile:full-potential@1.0"
        ),
        "tasks": tasks,
        "trimmed_domains": [],
        "budget_suggestion": 6,
        "deadline_suggestion_seconds": 600,
        "completion_policy": "ALLOW_PARTIAL_OPTIONAL" if mode == "INVESTMENT_REVIEW" else "REQUIRE_ALL",
        "replan_reason": None,
    }


def _handoff(objects: _Objects, run_id: UUID, task_row, *, epoch: int | None = None) -> dict[str, object]:
    agent = str(task_row["agent_identity_ref"]).split("@", 1)[0]
    task_id = task_row["id"]
    evidence_key = f"evidence/{task_id}.json"
    evidence_sha = hashlib.sha256(evidence_key.encode()).hexdigest()
    objects.add(evidence_key, evidence_sha)
    finding_document = {
        "finding_id": str(uuid4()),
        "agent_code": agent,
        "dimension": agent,
        "subdimension": "time-region",
        "claim": f"traceable claim from {agent}",
        "grade": "MODERATE",
        "score_input": 3.5,
        "evidence_refs": [evidence_key],
        "confidence": 0.8,
        "limitations": [],
        "region_scope": ["Hong Kong"],
        "as_of": date.today().isoformat(),
        "valid_until": date(date.today().year + 1, date.today().month, date.today().day).isoformat(),
        "hypothesis": False,
        "report_section_ref": "section:assessment",
    }
    return {
        "schema_version": "3.0",
        "tenant_id": str(task_row["tenant_id"]),
        "run_id": str(run_id),
        "task_id": str(task_id),
        "dispatch_epoch": int(task_row["dispatch_epoch"] if epoch is None else epoch),
        "agent_code": agent,
        "status": "SUCCEEDED",
        "findings": [finding_document],
        "report_ref": {"ref": evidence_key, "sha256": evidence_sha},
        "evidence_refs": [{"ref": evidence_key, "sha256": evidence_sha}],
        "limitations": [],
        "confidence": 0.8,
        "failure_class": None,
        "next_action": "continue to serial audit",
    }


def _specialist_report(records, task_row, product_title: str) -> dict[str, object]:
    agent = str(task_row["agent_identity_ref"]).split("@", 1)[0]
    claim_id = f"claim-{agent}-integration"
    return {
        "schema_version": "2.0",
        "report_id": str(uuid4()),
        "run_id": str(records["run_id"]),
        "project_id": str(records["project_id"]),
        "product_version_id": str(records["version_id"]),
        "product_title": product_title,
        "agent_code": agent,
        "source_sha256": "9" * 64,
        "executive_summary": [claim_id],
        "metrics": [],
        "claims": [
            {
                "claim_id": claim_id,
                "section": "INTEGRATION",
                "text": f"{agent} 待补充真实运行证据。",
                "status": "PENDING_VALIDATION",
                "decision_relevance": "IMPORTANT",
                "citation_ids": [],
                "score_bearing": False,
            }
        ],
        "domain_payload": {"test": "recorded-integration"},
        "risks": [claim_id],
        "actions": [
            {
                "action_id": f"action-{agent}-integration",
                "title": "补充真实证据",
                "owner": "项目负责人",
                "deadline_days": 14,
                "success_criteria": ["证据可追溯"],
                "failure_triggers": ["仍无直接证据"],
                "required_evidence": ["运行或用户记录"],
                "related_claim_ids": [claim_id],
            }
        ],
        "citations": [],
        "source_directory": [],
        "audit_summary": {"verified": 0, "insufficient": 0, "needs_more": 1, "conflicted": 0},
        "raw_audit_refs": [],
    }


def _audit(
    finding_row,
    run_id: UUID,
    round_number: int,
    decision: str,
    *,
    report_v2: bool = False,
) -> dict[str, object]:
    source = finding_row["structured_result"]["finding"]
    target = None
    if decision == "NEEDS_MORE":
        target = {
            "agent_code": finding_row["submitted_by"],
            "finding_id": str(finding_row["id"]),
            "question": "Provide one more direct source",
            "required_evidence": "one current region-specific source",
        }
    document: dict[str, object] = {
        "schema_version": "4.0" if report_v2 else "3.0",
        "audit_id": str(uuid4()),
        "run_id": str(run_id),
        "finding_id": str(finding_row["id"]),
        "source_finding_sha256": hashlib.sha256(_canonical(source)).hexdigest(),
        "decision": decision,
        "reason": "independent evidence calibration",
        "rule_ids": ["KB-EVD-001"],
        "conflict_group_ids": [],
        "freshness_status": "VALID",
        "audit_round": round_number,
        "remediation_target": target,
    }
    if report_v2:
        document.update(
            freshness_score=1.0,
            support_strength="MODERATE",
            independent_source_count=1,
            evidence_ids=[],
            source_locator_ids=[],
            citation_status=(
                "PENDING_VALIDATION"
                if decision == "NEEDS_MORE"
                else "REJECTED"
                if decision == "REJECTED"
                else "DOWNGRADED"
                if decision == "DOWNGRADED"
                else "VERIFIED"
            ),
            score_bearing=decision in {"ACCEPTED", "DOWNGRADED"},
        )
    return document


def _setup(database, runtime_engine, records, monkeypatch, mode: str = "FULL_POTENTIAL"):
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "true")
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "integration-capability-secret")
    brief_id, planning_task_id = _seed_planning(database, records, mode)
    actor = Actor(records["tenant_id"], "integration-supervisor-audit")
    accepted = ManagerPlanningApplication(session_factory(runtime_engine)).accept_and_materialize(
        actor, records["run_id"], planning_task_id, _plan(records["run_id"], brief_id, mode)
    )
    with database.begin() as connection:
        connection.execute(update(task).where(task.c.id.in_(accepted.task_ids)).values(status="RUNNING"))
        for task_id in accepted.task_ids:
            evidence_key = f"evidence/{task_id}.json"
            connection.execute(
                evidence.insert().values(
                    id=uuid4(),
                    tenant_id=records["tenant_id"],
                    run_id=records["run_id"],
                    task_id=task_id,
                    source_type="AGENT_REPORT",
                    object_key=evidence_key,
                    sha256=hashlib.sha256(evidence_key.encode()).hexdigest(),
                    mime_type="application/json",
                    evidence_level="E1",
                    trust_level="E1",
                )
            )
    return actor, accepted


def _activate_agent(database, run_id: UUID, agent_ref: str) -> None:
    with database.begin() as connection:
        connection.execute(
            update(task)
            .where(task.c.run_id == run_id, task.c.agent_identity_ref == agent_ref, task.c.status == "READY")
            .values(status="RUNNING")
        )


def test_v6_domain_ingestion_preserves_submitted_specialist_report_body(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, accepted = _setup(database, runtime_engine, tenant_records, monkeypatch)
    objects = _Objects()
    application = SupervisorAuditApplication(session_factory(runtime_engine), objects)
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        product_title = session.execute(
            select(project.c.name).where(project.c.id == tenant_records["project_id"])
        ).scalar_one()
        session.execute(
            update(task).where(task.c.id.in_(accepted.task_ids)).values(agent_identity_ref="product-engineering@6.0")
        )
        task_row = session.execute(select(task).where(task.c.id == accepted.task_ids[0])).mappings().one()
    report_document = _specialist_report(tenant_records, task_row, product_title)
    report_body = _canonical(report_document)
    report_sha = hashlib.sha256(report_body).hexdigest()
    document = _handoff(objects, tenant_records["run_id"], task_row)
    document["report_ref"] = None

    application.ingest_domain_handoff(
        actor,
        tenant_records["run_id"],
        task_row["id"],
        document,
        specialist_report=report_document,
    )

    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        artifact = (
            session.execute(select(agent_report_artifact).where(agent_report_artifact.c.task_id == task_row["id"]))
            .mappings()
            .one()
        )
    assert artifact["id"] == UUID(str(report_document["report_id"]))
    assert artifact["sha256"] == report_sha
    assert objects.bodies[artifact["object_key"]] == report_body


def test_v6_report_v3_ingestion_does_not_revalidate_the_persisted_v3_body_as_v2(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, accepted = _setup(database, runtime_engine, tenant_records, monkeypatch)
    objects = _Objects()
    application = SupervisorAuditApplication(session_factory(runtime_engine), objects)
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        product_title = session.execute(
            select(project.c.name).where(project.c.id == tenant_records["project_id"])
        ).scalar_one()
        state_flags = session.execute(
            select(evaluation_run.c.state_flags).where(evaluation_run.c.id == tenant_records["run_id"])
        ).scalar_one() or {}
        session.execute(
            update(evaluation_run)
            .where(evaluation_run.c.id == tenant_records["run_id"])
            .values(
                state_flags={
                    **state_flags,
                    "architecture_generation": "supervisor-1p4-report-v3",
                    "locale": "zh-CN",
                }
            )
        )
        session.execute(
            update(task).where(task.c.id.in_(accepted.task_ids)).values(agent_identity_ref="product-engineering@6.0")
        )
        task_row = session.execute(select(task).where(task.c.id == accepted.task_ids[0])).mappings().one()
    document = _handoff(objects, tenant_records["run_id"], task_row)
    document["report_ref"] = None

    application.ingest_domain_handoff(
        actor,
        tenant_records["run_id"],
        task_row["id"],
        document,
        specialist_report=_specialist_report(tenant_records, task_row, product_title),
    )

    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        artifact = session.execute(
            select(agent_report_artifact).where(agent_report_artifact.c.task_id == task_row["id"])
        ).mappings().one()
    persisted = json.loads(objects.bodies[artifact["object_key"]])
    assert persisted["schema_version"] == "3.0"
    assert persisted["locale"] == "zh-CN"


def test_v6_domain_ingestion_accepts_repeated_reads_of_the_same_immutable_object(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, accepted = _setup(database, runtime_engine, tenant_records, monkeypatch)
    objects = _Objects()
    application = SupervisorAuditApplication(session_factory(runtime_engine), objects)
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        product_title = session.execute(
            select(project.c.name).where(project.c.id == tenant_records["project_id"])
        ).scalar_one()
        session.execute(
            update(task).where(task.c.id.in_(accepted.task_ids)).values(agent_identity_ref="product-engineering@6.0")
        )
        task_row = session.execute(select(task).where(task.c.id == accepted.task_ids[0])).mappings().one()
    evidence_key = f"evidence/{task_row['id']}.json"
    with database.begin() as connection:
        connection.execute(
            evidence.insert().values(
                id=uuid4(),
                tenant_id=tenant_records["tenant_id"],
                run_id=tenant_records["run_id"],
                task_id=task_row["id"],
                source_type="MATERIAL_UNIT",
                object_key=evidence_key,
                sha256=hashlib.sha256(evidence_key.encode()).hexdigest(),
                mime_type="application/json",
                evidence_level="E1",
                trust_level="E1",
            )
        )

    result = application.ingest_domain_handoff(
        actor,
        tenant_records["run_id"],
        task_row["id"],
        {**_handoff(objects, tenant_records["run_id"], task_row), "report_ref": None},
        specialist_report=_specialist_report(tenant_records, task_row, product_title),
    )

    assert result.state == "DOMAIN_REVIEW"


def test_v6_domain_success_requires_reading_every_required_material(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, accepted = _setup(database, runtime_engine, tenant_records, monkeypatch)
    objects = _Objects()
    application = SupervisorAuditApplication(session_factory(runtime_engine), objects)
    now = datetime.now(UTC)
    material_id, analysis_id, unit_id = uuid4(), uuid4(), uuid4()
    with database.begin() as connection:
        task_id = accepted.task_ids[0]
        plan_id = connection.execute(
            select(agent_task_ticket.c.plan_id).where(agent_task_ticket.c.task_id == task_id)
        ).scalar_one()
        connection.execute(
            update(task).where(task.c.id == task_id).values(agent_identity_ref="product-engineering@6.0")
        )
        connection.execute(
            material.insert().values(
                id=material_id,
                tenant_id=tenant_records["tenant_id"],
                product_version_id=tenant_records["version_id"],
                source_type="UPLOAD",
                object_key=f"material/{material_id}.txt",
                sha256="a" * 64,
                size_bytes=10,
                mime_type="text/plain",
                display_name="required.txt",
                trust_level="T1",
                ingest_status="VALIDATED",
                object_metadata={},
                submitted_at=now,
                created_at=now,
            )
        )
        connection.execute(
            material_analysis.insert().values(
                id=analysis_id,
                tenant_id=tenant_records["tenant_id"],
                material_id=material_id,
                product_version_id=tenant_records["version_id"],
                status="READY",
                attempt=0,
                parser_version="test",
                page_count=1,
                unit_count=1,
                coverage={"uncovered_locators": []},
                external_consent=False,
                created_at=now,
                updated_at=now,
                completed_at=now,
            )
        )
        connection.execute(
            material_unit.insert().values(
                id=unit_id,
                tenant_id=tenant_records["tenant_id"],
                analysis_id=analysis_id,
                material_id=material_id,
                product_version_id=tenant_records["version_id"],
                ordinal=1,
                unit_type="PARAGRAPH",
                locator={"paragraph": 1},
                tags=[],
                confidence=1,
                contains_sensitive_data=False,
                object_key=f"unit/{unit_id}.json",
                sha256="b" * 64,
                summary="required material",
                created_at=now,
            )
        )
        connection.execute(
            task_material_scope.insert().values(
                id=uuid4(),
                tenant_id=tenant_records["tenant_id"],
                run_id=tenant_records["run_id"],
                task_id=task_id,
                plan_id=plan_id,
                material_id=material_id,
                analysis_id=analysis_id,
                unit_ids=[str(unit_id)],
                unit_refs=[f"material-unit:{unit_id}@{'b' * 64}"],
                reason="required material",
                required=True,
                scope_sha256="c" * 64,
                created_at=now,
            )
        )
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        product_title = session.execute(
            select(project.c.name).where(project.c.id == tenant_records["project_id"])
        ).scalar_one()
        task_row = session.execute(select(task).where(task.c.id == accepted.task_ids[0])).mappings().one()

    result = application.ingest_domain_handoff(
        actor,
        tenant_records["run_id"],
        task_row["id"],
        _handoff(objects, tenant_records["run_id"], task_row),
        specialist_report=_specialist_report(tenant_records, task_row, product_title),
    )

    assert result.state == "DOMAIN_REVIEW"
    with database.connect() as connection:
        stored = connection.execute(
            select(task.c.status, task.c.last_failure_class).where(task.c.id == task_row["id"])
        ).one()
        report_count = len(
            connection.execute(
                select(agent_report_artifact.c.id).where(agent_report_artifact.c.task_id == task_row["id"])
            ).all()
        )
    assert stored == ("KNOWN_FAILED", "REQUIRED_MATERIAL_NOT_READ")
    assert report_count == 0


def test_known_failure_class_uses_full_handoff_contract_width(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, accepted = _setup(database, runtime_engine, tenant_records, monkeypatch)
    objects = _Objects()
    application = SupervisorAuditApplication(session_factory(runtime_engine), objects)
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        task_row = session.execute(select(task).where(task.c.id == accepted.task_ids[0])).mappings().one()
    document = _handoff(objects, tenant_records["run_id"], task_row)
    failure_class = "specialist_report_runtime_budget_exhausted"
    document.update(
        status="KNOWN_FAILED",
        findings=[],
        report_ref=None,
        evidence_refs=[],
        failure_class=failure_class,
        next_action="resume the same task after repairing the packaged runtime",
    )

    application.ingest_domain_handoff(actor, tenant_records["run_id"], task_row["id"], document)

    with database.connect() as connection:
        stored = connection.execute(
            select(task.c.status, task.c.last_failure_class).where(task.c.id == task_row["id"])
        ).one()
    assert stored == ("KNOWN_FAILED", failure_class)


def test_v6_auditor_persists_its_own_specialist_report(database, runtime_engine, tenant_records, monkeypatch) -> None:
    actor, accepted = _setup(database, runtime_engine, tenant_records, monkeypatch)
    objects = _Objects()
    application = SupervisorAuditApplication(session_factory(runtime_engine), objects)
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        product_title = session.execute(
            select(project.c.name).where(project.c.id == tenant_records["project_id"])
        ).scalar_one()
        manifest = session.execute(
            select(run_manifest.c.frozen_config).where(run_manifest.c.run_id == tenant_records["run_id"])
        ).scalar_one()
        session.execute(
            update(run_manifest)
            .where(run_manifest.c.run_id == tenant_records["run_id"])
            .values(
                frozen_config={
                    **manifest,
                    "architecture_generation": "supervisor-1p4-report-v22",
                    "agent_contract_generation": "v6",
                }
            )
        )
        rows = session.execute(select(task).where(task.c.id.in_(accepted.task_ids))).mappings().all()
        for row in rows:
            agent = str(row["agent_identity_ref"]).split("@", 1)[0]
            session.execute(update(task).where(task.c.id == row["id"]).values(agent_identity_ref=f"{agent}@6.0"))
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        rows = session.execute(select(task).where(task.c.id.in_(accepted.task_ids))).mappings().all()
    for task_row in rows:
        report_document = _specialist_report(tenant_records, task_row, product_title)
        report_body = _canonical(report_document)
        report_sha = hashlib.sha256(report_body).hexdigest()
        report_key = f"specialist-report/{task_row['id']}.json"
        objects.values[report_key] = _Metadata(report_sha)
        objects.bodies[report_key] = report_body
        with database.begin() as connection:
            connection.execute(
                evidence.insert().values(
                    id=uuid4(),
                    tenant_id=tenant_records["tenant_id"],
                    run_id=tenant_records["run_id"],
                    task_id=task_row["id"],
                    source_type="AGENT_REPORT",
                    object_key=report_key,
                    sha256=report_sha,
                    mime_type="application/json",
                    evidence_level="E1",
                    trust_level="E1",
                )
            )
        handoff = _handoff(objects, tenant_records["run_id"], task_row)
        handoff["report_ref"] = {"ref": report_key, "sha256": report_sha}
        handoff["evidence_refs"].append({"ref": report_key, "sha256": report_sha})
        objects.values[report_key] = _Metadata(report_sha)
        objects.bodies[report_key] = report_body
        application.ingest_domain_handoff(actor, tenant_records["run_id"], task_row["id"], handoff)
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        auditor = (
            session.execute(
                select(task).where(
                    task.c.run_id == tenant_records["run_id"],
                    task.c.agent_identity_ref == "evidence-auditor@6.0",
                )
            )
            .mappings()
            .one()
        )
        findings = session.execute(select(finding).where(finding.c.run_id == tenant_records["run_id"])).mappings().all()
        session.execute(update(task).where(task.c.id == auditor["id"]).values(status="RUNNING"))
    audit_document = _specialist_report(tenant_records, auditor, product_title)
    audit_body = _canonical(audit_document)
    audit_sha = hashlib.sha256(audit_body).hexdigest()

    application.submit_audit_results(
        actor,
        tenant_records["run_id"],
        [_audit(row, tenant_records["run_id"], 1, "ACCEPTED", report_v2=True) for row in findings],
        task_id=auditor["id"],
        specialist_report=audit_document,
    )

    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        artifact = (
            session.execute(select(agent_report_artifact).where(agent_report_artifact.c.task_id == auditor["id"]))
            .mappings()
            .one()
        )
    assert artifact["id"] == UUID(str(audit_document["report_id"]))
    assert artifact["sha256"] == audit_sha
    assert objects.bodies[artifact["object_key"]] == audit_body


def test_resumed_domain_task_still_unlocks_serial_audit(database, runtime_engine, tenant_records, monkeypatch) -> None:
    actor, accepted = _setup(database, runtime_engine, tenant_records, monkeypatch)
    objects = _Objects()
    application = SupervisorAuditApplication(session_factory(runtime_engine), objects)
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        session.execute(update(task).where(task.c.id.in_(accepted.task_ids)).values(dispatch_epoch=1))
        domain_tasks = session.execute(select(task).where(task.c.id.in_(accepted.task_ids))).mappings().all()
    for task_row in domain_tasks:
        application.ingest_domain_handoff(
            actor, tenant_records["run_id"], task_row["id"], _handoff(objects, tenant_records["run_id"], task_row)
        )
    assert application.reconcile_domain_completion(actor, tenant_records["run_id"]) == "EVIDENCE_AUDIT"
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        auditor = (
            session.execute(
                select(task).where(
                    task.c.run_id == tenant_records["run_id"],
                    task.c.agent_identity_ref == "evidence-auditor@4.0",
                )
            )
            .mappings()
            .one()
        )
        findings = session.execute(
            select(finding).where(finding.c.run_id == tenant_records["run_id"])
        ).mappings().all()
    assert auditor["status"] == "READY"
    _activate_agent(database, tenant_records["run_id"], "evidence-auditor@4.0")
    result = application.submit_audit_results(
        actor,
        tenant_records["run_id"],
        [_audit(row, tenant_records["run_id"], 1, "ACCEPTED") for row in findings],
    )
    assert result.state == "DETERMINISTIC_SCORING"


def test_needs_more_creates_one_remediation_round_and_one_reaudit(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, accepted = _setup(database, runtime_engine, tenant_records, monkeypatch)
    objects = _Objects()
    application = SupervisorAuditApplication(session_factory(runtime_engine), objects)
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        domain_tasks = session.execute(select(task).where(task.c.id.in_(accepted.task_ids))).mappings().all()
    for task_row in domain_tasks:
        application.ingest_domain_handoff(
            actor, tenant_records["run_id"], task_row["id"], _handoff(objects, tenant_records["run_id"], task_row)
        )
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        domain_catalog = (
            session.execute(
                select(agent_report_artifact).where(
                    agent_report_artifact.c.run_id == tenant_records["run_id"],
                    agent_report_artifact.c.report_kind == "DOMAIN",
                )
            )
            .mappings()
            .all()
        )
    assert {item["agent_code"] for item in domain_catalog} == {
        "user-evidence",
        "product-engineering",
        "business-investment",
    }
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        first_findings = (
            session.execute(
                select(finding).where(
                    finding.c.tenant_id == tenant_records["tenant_id"], finding.c.run_id == tenant_records["run_id"]
                )
            )
            .mappings()
            .all()
        )
    first_documents = [
        _audit(row, tenant_records["run_id"], 1, "NEEDS_MORE" if index == 0 else "ACCEPTED")
        for index, row in enumerate(first_findings)
    ]
    _activate_agent(database, tenant_records["run_id"], "evidence-auditor@4.0")
    first_audit = application.submit_audit_results(actor, tenant_records["run_id"], first_documents)
    assert first_audit.state == "TARGETED_REMEDIATION" and len(first_audit.remediation_task_ids) == 1
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        remediation = (
            session.execute(select(task).where(task.c.id == first_audit.remediation_task_ids[0])).mappings().one()
        )
        source = (
            session.execute(
                select(task).where(
                    task.c.run_id == tenant_records["run_id"],
                    task.c.agent_identity_ref == remediation["agent_identity_ref"],
                    task.c.dispatch_epoch == 0,
                )
            )
            .mappings()
            .one()
        )
    assert remediation["tool_allowlist"] == source["tool_allowlist"]
    assert len(remediation["tool_allowlist"]) == 2
    remediation_key = f"evidence/{remediation['id']}.json"
    with database.begin() as connection:
        connection.execute(
            evidence.insert().values(
                id=uuid4(),
                tenant_id=tenant_records["tenant_id"],
                run_id=tenant_records["run_id"],
                task_id=remediation["id"],
                source_type="AGENT_REPORT",
                object_key=remediation_key,
                sha256=hashlib.sha256(remediation_key.encode()).hexdigest(),
                mime_type="application/json",
                evidence_level="E1",
                trust_level="E1",
            )
        )
    _activate_agent(database, tenant_records["run_id"], str(remediation["agent_identity_ref"]))
    reused_finding_handoff = _handoff(objects, tenant_records["run_id"], remediation, epoch=1)
    reused_finding_handoff["findings"][0]["finding_id"] = first_documents[0]["finding_id"]
    with pytest.raises(SupervisorWorkflowError, match="new finding_id"):
        application.ingest_domain_handoff(
            actor,
            tenant_records["run_id"],
            remediation["id"],
            reused_finding_handoff,
        )
    application.ingest_domain_handoff(
        actor,
        tenant_records["run_id"],
        remediation["id"],
        _handoff(objects, tenant_records["run_id"], remediation, epoch=1),
    )
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        second_auditor = (
            session.execute(
                select(task).where(
                    task.c.run_id == tenant_records["run_id"],
                    task.c.agent_identity_ref == "evidence-auditor@4.0",
                    task.c.dispatch_epoch == 1,
                )
            )
            .mappings()
            .one()
        )
        second_auditor_ticket = (
            session.execute(select(agent_task_ticket).where(agent_task_ticket.c.task_id == second_auditor["id"]))
            .mappings()
            .one()
        )
        all_findings = (
            session.execute(
                select(finding).where(
                    finding.c.tenant_id == tenant_records["tenant_id"], finding.c.run_id == tenant_records["run_id"]
                )
            )
            .mappings()
            .all()
        )
    assert second_auditor_ticket["dispatch_epoch"] == 1
    assert second_auditor_ticket["public_summary"]["dispatch_epoch"] == 1
    assert second_auditor_ticket["public_summary"]["success_conditions"][-1].endswith("audit_round to 2")
    second_documents = [_audit(row, tenant_records["run_id"], 2, "ACCEPTED") for row in all_findings]
    _activate_agent(database, tenant_records["run_id"], "evidence-auditor@4.0")
    second_audit = application.submit_audit_results(actor, tenant_records["run_id"], second_documents)
    assert second_audit.state == "DETERMINISTIC_SCORING" and second_audit.remediation_task_ids == ()
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        audits = (
            session.execute(
                select(evidence_audit).where(
                    evidence_audit.c.tenant_id == tenant_records["tenant_id"],
                    evidence_audit.c.run_id == tenant_records["run_id"],
                )
            )
            .mappings()
            .all()
        )
        stage_rows = (
            session.execute(
                select(stage.c.code, stage.c.status).where(
                    stage.c.tenant_id == tenant_records["tenant_id"], stage.c.run_id == tenant_records["run_id"]
                )
            )
            .mappings()
            .all()
        )
        artifacts = (
            session.execute(
                select(agent_report_artifact).where(
                    agent_report_artifact.c.tenant_id == tenant_records["tenant_id"],
                    agent_report_artifact.c.run_id == tenant_records["run_id"],
                )
            )
            .mappings()
            .all()
        )
    assert {row["audit_round"] for row in audits} == {1, 2}
    assert all(row["contract_version"] == "4.0" for row in audits)
    assert all(row["score_components"]["citation_status"] in {"VERIFIED", "PENDING_VALIDATION"} for row in audits)
    assert all(isinstance(row["score_components"]["source_locator_ids"], list) for row in audits)
    stage_statuses = {row["code"]: row["status"] for row in stage_rows}
    assert stage_statuses["DOMAIN_REVIEW"] == "COMPLETED"
    assert stage_statuses["TARGETED_REMEDIATION"] == "COMPLETED"
    assert stage_statuses["EVIDENCE_AUDIT"] == "COMPLETED"
    assert {row["revision"] for row in artifacts if row["agent_code"] == "evidence-auditor"} == {1, 2}
    assert all(row["status"] == "AVAILABLE" for row in artifacts)
    assert len(first_findings) == 3 and len(all_findings) == 4
    original_ids = {item["id"] for item in first_findings}
    assert {row["id"]: row["structured_result"] for row in first_findings} == {
        row["id"]: row["structured_result"] for row in all_findings if row["id"] in original_ids
    }
    assert next(row for row in all_findings if row["supersedes_id"] is not None)["supersedes_id"] in {
        row["id"] for row in first_findings
    }


def test_submission_unknown_is_persisted_needs_attention_without_retry(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, accepted = _setup(database, runtime_engine, tenant_records, monkeypatch)
    objects = _Objects()
    application = SupervisorAuditApplication(session_factory(runtime_engine), objects)
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        assigned = session.execute(select(task).where(task.c.id == accepted.task_ids[0])).mappings().one()
    document = _handoff(objects, tenant_records["run_id"], assigned)
    document.update(
        status="SUBMISSION_UNKNOWN",
        findings=[],
        report_ref=None,
        evidence_refs=[],
        failure_class="SUBMISSION_UNKNOWN",
        next_action="operator reconciliation required",
    )
    result = application.ingest_domain_handoff(actor, tenant_records["run_id"], assigned["id"], document)
    assert result.state == "NEEDS_ATTENTION"
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        run = (
            session.execute(select(evaluation_run).where(evaluation_run.c.id == tenant_records["run_id"]))
            .mappings()
            .one()
        )
        changed_task = session.execute(select(task).where(task.c.id == assigned["id"])).mappings().one()
    assert run["status"] == "NEEDS_ATTENTION" and run["last_failure_class"] == "SUBMISSION_UNKNOWN"
    assert changed_task["status"] == "NEEDS_ATTENTION" and changed_task["transient_retries"] == 0


def test_unknown_audit_report_write_fails_closed_without_catalog_entry(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, accepted = _setup(database, runtime_engine, tenant_records, monkeypatch)
    objects = _Objects()
    application = SupervisorAuditApplication(session_factory(runtime_engine), objects)
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        domain_tasks = session.execute(select(task).where(task.c.id.in_(accepted.task_ids))).mappings().all()
    for task_row in domain_tasks:
        application.ingest_domain_handoff(
            actor, tenant_records["run_id"], task_row["id"], _handoff(objects, tenant_records["run_id"], task_row)
        )
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        findings = session.execute(select(finding).where(finding.c.run_id == tenant_records["run_id"])).mappings().all()
    objects.fail_writes = True

    _activate_agent(database, tenant_records["run_id"], "evidence-auditor@4.0")
    result = application.submit_audit_results(
        actor,
        tenant_records["run_id"],
        [_audit(row, tenant_records["run_id"], 1, "ACCEPTED") for row in findings],
    )

    assert result.state == "NEEDS_ATTENTION"
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        run = session.execute(select(evaluation_run).where(evaluation_run.c.id == tenant_records["run_id"])).one()
        audit_artifacts = session.execute(
            select(agent_report_artifact.c.id).where(
                agent_report_artifact.c.run_id == tenant_records["run_id"],
                agent_report_artifact.c.agent_code == "evidence-auditor",
            )
        ).all()
    assert run.status == "NEEDS_ATTENTION" and run.last_failure_class == "REPORT_PERSISTENCE_UNKNOWN"
    assert audit_artifacts == []


def test_known_optional_failure_allows_partial_audit_without_fabricated_findings(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, accepted = _setup(database, runtime_engine, tenant_records, monkeypatch, "INVESTMENT_REVIEW")
    objects = _Objects()
    application = SupervisorAuditApplication(session_factory(runtime_engine), objects)
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        domain_tasks = session.execute(select(task).where(task.c.id.in_(accepted.task_ids))).mappings().all()
    failed_agent = "user-evidence"
    for task_row in sorted(domain_tasks, key=lambda item: item["agent_identity_ref"] != f"{failed_agent}@4.0"):
        document = _handoff(objects, tenant_records["run_id"], task_row)
        if task_row["agent_identity_ref"] == f"{failed_agent}@4.0":
            document.update(
                status="KNOWN_FAILED",
                findings=[],
                report_ref=None,
                evidence_refs=[],
                failure_class="KNOWN_PROVIDER_REJECTION",
                next_action="record the optional coverage gap",
            )
        application.ingest_domain_handoff(actor, tenant_records["run_id"], task_row["id"], document)
    application.reconcile_domain_completion(actor, tenant_records["run_id"])
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        available = (
            session.execute(
                select(finding).where(
                    finding.c.tenant_id == tenant_records["tenant_id"], finding.c.run_id == tenant_records["run_id"]
                )
            )
            .mappings()
            .all()
        )
    assert len(available) == 2 and all(row["submitted_by"] != failed_agent for row in available)
    _activate_agent(database, tenant_records["run_id"], "evidence-auditor@4.0")
    result = application.submit_audit_results(
        actor,
        tenant_records["run_id"],
        [_audit(row, tenant_records["run_id"], 1, "ACCEPTED") for row in available],
    )
    assert result.state == "DETERMINISTIC_SCORING"


def test_required_agent_failure_stops_before_complete_conclusion(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, accepted = _setup(database, runtime_engine, tenant_records, monkeypatch)
    with database.begin() as connection:
        connection.execute(
            update(evaluation_run)
            .where(evaluation_run.c.id == tenant_records["run_id"])
            .values(status="RUNNING", current_stage="DOMAIN_REVIEW")
        )
    objects = _Objects()
    application = SupervisorAuditApplication(session_factory(runtime_engine), objects)
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        domain_tasks = session.execute(select(task).where(task.c.id.in_(accepted.task_ids))).mappings().all()
    for task_row in domain_tasks:
        document = _handoff(objects, tenant_records["run_id"], task_row)
        if task_row["agent_identity_ref"] == "business-investment@4.0":
            document.update(
                status="KNOWN_FAILED",
                findings=[],
                report_ref=None,
                evidence_refs=[],
                failure_class="KNOWN_PROVIDER_REJECTION",
                next_action="required Agent must be restored",
            )
        application.ingest_domain_handoff(actor, tenant_records["run_id"], task_row["id"], document)
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        available = (
            session.execute(
                select(finding).where(
                    finding.c.tenant_id == tenant_records["tenant_id"], finding.c.run_id == tenant_records["run_id"]
                )
            )
            .mappings()
            .all()
        )
        domain_states = session.execute(
            select(task.c.agent_identity_ref, task.c.stage_code, task.c.status, task.c.required).where(
                task.c.id.in_(accepted.task_ids)
            )
        ).all()
        pre_reconcile_run = session.execute(
            select(evaluation_run.c.status, evaluation_run.c.current_stage).where(
                evaluation_run.c.id == tenant_records["run_id"]
            )
        ).one()
    assert len(available) == 2
    assert len(domain_states) == 3 and {row.status for row in domain_states} == {"SUCCEEDED", "KNOWN_FAILED"}
    assert {row.agent_identity_ref for row in domain_states} == {
        "user-evidence@4.0",
        "product-engineering@4.0",
        "business-investment@4.0",
    }
    assert {row.stage_code for row in domain_states} == {"DOMAIN_REVIEW"}
    assert all(row.required for row in domain_states)
    assert pre_reconcile_run in {("RUNNING", "DOMAIN_REVIEW"), ("NEEDS_ATTENTION", "NEEDS_ATTENTION")}
    state = application.reconcile_domain_completion(actor, tenant_records["run_id"])
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        run = session.execute(select(evaluation_run).where(evaluation_run.c.id == tenant_records["run_id"])).one()
        auditor_tasks = session.execute(
            select(task.c.id).where(
                task.c.run_id == tenant_records["run_id"],
                task.c.agent_identity_ref == "evidence-auditor@4.0",
            )
        ).all()
    assert state == "NEEDS_ATTENTION"
    assert run.status == "NEEDS_ATTENTION" and run.attention_reason == "REQUIRED_AGENT_FAILED"
    assert auditor_tasks == []


def _audited_run(database, runtime_engine, records, monkeypatch):
    actor, accepted = _setup(database, runtime_engine, records, monkeypatch)
    objects = _Objects()
    audit_application = SupervisorAuditApplication(session_factory(runtime_engine), objects)
    with tenant_transaction(session_factory(runtime_engine), records["scope"]) as session:
        domain_tasks = session.execute(select(task).where(task.c.id.in_(accepted.task_ids))).mappings().all()
    for task_row in domain_tasks:
        audit_application.ingest_domain_handoff(
            actor, records["run_id"], task_row["id"], _handoff(objects, records["run_id"], task_row)
        )
    with tenant_transaction(session_factory(runtime_engine), records["scope"]) as session:
        findings = (
            session.execute(
                select(finding).where(
                    finding.c.tenant_id == records["tenant_id"], finding.c.run_id == records["run_id"]
                )
            )
            .mappings()
            .all()
        )
    _activate_agent(database, records["run_id"], "evidence-auditor@4.0")
    audit_application.submit_audit_results(
        actor,
        records["run_id"],
        [_audit(row, records["run_id"], 1, "ACCEPTED") for row in findings],
    )
    return actor, objects, findings


def test_inflight_legacy_run_does_not_switch_report_generation_after_flag_enable(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, objects, _findings = _audited_run(database, runtime_engine, tenant_records, monkeypatch)
    monkeypatch.setenv("LAUNCHSCOPE_REPORT_V2_ENABLED", "true")

    preparation = SupervisorCompletionApplication(session_factory(runtime_engine), objects).prepare_scoring(
        actor, tenant_records["run_id"]
    )

    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        synthesis_task = session.execute(
            select(task).where(task.c.id == preparation.synthesis_task_id)
        ).mappings().one()
        ticket = session.execute(
            select(agent_task_ticket).where(agent_task_ticket.c.task_id == preparation.synthesis_task_id)
        ).mappings().one()
        score_row = session.execute(select(decision).where(decision.c.id == preparation.decision_id)).mappings().one()
    assert synthesis_task["agent_identity_ref"] == "evaluation-manager@4.0"
    assert ticket["public_summary"]["report_contract"] == "domain-report-ref.v1"
    assert score_row["dimension_grades"]["score_profile_ref"] == "score-profile:full-potential@1.0"


def _synthesis(preparation, run_id: UUID, finding_rows, *, nonexistent_citation: bool = False):
    deterministic = preparation.score.recommendation
    proposed = "ADJUST" if deterministic != "ADJUST" else "PROCEED"
    finding_refs = [str(row["id"]) for row in finding_rows]
    if nonexistent_citation:
        finding_refs[0] = str(uuid4())
    return {
        "schema_version": "1.0",
        "synthesis_id": str(uuid4()),
        "run_id": str(run_id),
        "deterministic_decision_ref": str(preparation.decision_id),
        "deterministic_recommendation": deterministic,
        "proposed_recommendation": proposed,
        "summary": "Audited cross-domain synthesis; deterministic decision remains authoritative.",
        "cross_domain_analysis": ["All three first-round domains were independently audited."],
        "risks": ["Evidence remains time bounded."],
        "conflicts": ["Manager recommendation differs from the deterministic recommendation."],
        "actions": ["Execute the highest-priority validation action."],
        "version_changes": preparation.version_changes,
        "citations": [{"kind": "FINDING", "ref": reference} for reference in finding_refs],
        "decision_conflict": True,
    }


def _synthesis_v2(objects: _Objects, preparation, run_id: UUID) -> dict[str, object]:
    context = json.loads(objects.bodies[preparation.context_ref["ref"]])
    claims = []
    for index, item in enumerate(context["audit_layer"]):
        claims.append(
            {
                "claim_id": item["claim_id"],
                "section": "CONCLUSION" if index == 0 else "CROSS_DOMAIN",
                "text": item["claim"],
                "status": item.get("citation_status", "VERIFIED"),
                "decision_relevance": "CRITICAL" if index == 0 else "IMPORTANT",
                "citation_ids": item["citation_ids"],
                "score_bearing": bool(item["citation_ids"]),
            }
        )
    summary_claim_id = claims[0]["claim_id"]
    return {
        "schema_version": "2.0",
        "synthesis_id": str(uuid4()),
        "run_id": str(run_id),
        "deterministic_decision_ref": str(preparation.decision_id),
        "summary_claim_id": summary_claim_id,
        "claims": claims,
        "actions": [
            {
                "action_id": "action-validate-1",
                "title": "补充关键用户验证",
                "owner": "项目负责人",
                "deadline_days": 14,
                "success_criteria": ["获得可核验反馈"],
                "failure_triggers": ["关键假设未被验证"],
                "required_evidence": ["访谈或行为记录"],
                "related_claim_ids": [summary_claim_id],
            }
        ],
        "decision_conflict": False,
    }


def test_report_v2_commits_one_hash_bound_supervisor_document(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, objects, _finding_rows = _audited_run(database, runtime_engine, tenant_records, monkeypatch)
    monkeypatch.setenv("LAUNCHSCOPE_REPORT_V2_ENABLED", "true")
    with database.begin() as connection:
        manifest = connection.execute(
            select(run_manifest.c.frozen_config).where(run_manifest.c.run_id == tenant_records["run_id"])
        ).scalar_one()
        connection.execute(
            update(run_manifest)
            .where(run_manifest.c.run_id == tenant_records["run_id"])
            .values(
                frozen_config={
                    **manifest,
                    "architecture_generation": "supervisor-1p4-report-v22",
                    "agent_contract_generation": "v6",
                }
            )
        )
        connection.execute(
            update(evaluation_run)
            .where(evaluation_run.c.id == tenant_records["run_id"])
            .values(
                input_snapshot_sha256="c" * 64,
                content_fingerprint_sha256="d" * 64,
                report_profile_ref="supervisor-report@2.0",
                standard_version="2.2",
            )
        )
    completion = SupervisorCompletionApplication(session_factory(runtime_engine), objects)
    preparation = completion.prepare_scoring(actor, tenant_records["run_id"])
    _activate_agent(database, tenant_records["run_id"], "evaluation-manager@6.0")

    result = completion.commit_synthesis_report(
        actor,
        tenant_records["run_id"],
        preparation.synthesis_task_id,
        _synthesis_v2(objects, preparation, tenant_records["run_id"]),
    )

    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        report_row = session.execute(select(report).where(report.c.id == result.report_id)).mappings().one()
        citation_rows = (
            session.execute(select(report_claim_citation).where(report_claim_citation.c.report_id == result.report_id))
            .mappings()
            .all()
        )
        dossier_row = (
            session.execute(
                select(project_dossier_snapshot).where(project_dossier_snapshot.c.report_id == result.report_id)
            )
            .mappings()
            .one()
        )
    body = objects.bodies[report_row["object_key"]]
    document = json.loads(body)
    assert hashlib.sha256(body).hexdigest() == report_row["sha256"]
    assert document["schema_version"] == "2.0"
    assert document["top_card"]["recommendation"] == result.recommendation
    assert "comparison" not in document
    assert len(document["agent_report_cards"]) == 4
    assert len(citation_rows) == len(document["citations"])
    assert dossier_row["schema_version"] == "2.0" and dossier_row["document"]["report_sha256"] == report_row["sha256"]


def test_report_v3_commits_four_dimensions_and_a_matching_dossier_version(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, objects, _finding_rows = _audited_run(database, runtime_engine, tenant_records, monkeypatch)
    monkeypatch.setenv("LAUNCHSCOPE_REPORT_V3_ENABLED", "true")
    with database.begin() as connection:
        manifest = connection.execute(
            select(run_manifest.c.frozen_config).where(run_manifest.c.run_id == tenant_records["run_id"])
        ).scalar_one()
        state_flags = connection.execute(
            select(evaluation_run.c.state_flags).where(evaluation_run.c.id == tenant_records["run_id"])
        ).scalar_one() or {}
        connection.execute(
            update(run_manifest)
            .where(run_manifest.c.run_id == tenant_records["run_id"])
            .values(
                frozen_config={
                    **manifest,
                    "schema_version": "7.0",
                    "architecture_generation": "supervisor-1p4-report-v3",
                    "agent_contract_generation": "v6",
                }
            )
        )
        connection.execute(
            update(evaluation_run)
            .where(evaluation_run.c.id == tenant_records["run_id"])
            .values(
                state_flags={**state_flags, "architecture_generation": "supervisor-1p4-report-v3", "locale": "zh-CN"},
                input_snapshot_sha256="c" * 64,
                content_fingerprint_sha256="d" * 64,
                report_profile_ref="supervisor-report@3.0",
                standard_version="2.2",
            )
        )
    completion = SupervisorCompletionApplication(session_factory(runtime_engine), objects)
    preparation = completion.prepare_scoring(actor, tenant_records["run_id"])
    _activate_agent(database, tenant_records["run_id"], "evaluation-manager@6.0")

    result = completion.commit_synthesis_report(
        actor,
        tenant_records["run_id"],
        preparation.synthesis_task_id,
        _synthesis_v2(objects, preparation, tenant_records["run_id"]),
    )

    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        report_row = session.execute(select(report).where(report.c.id == result.report_id)).mappings().one()
        dossier_row = (
            session.execute(
                select(project_dossier_snapshot).where(project_dossier_snapshot.c.report_id == result.report_id)
            )
            .mappings()
            .one()
        )
    document = json.loads(objects.bodies[report_row["object_key"]])
    assert document["schema_version"] == "3.0"
    assert document["locale"] == "zh-CN"
    assert set(document["dimension_scores"]) == {
        "user_value",
        "product_capability",
        "investment_potential",
        "evidence_quality",
    }
    assert document["top_card"]["recommendation"] == result.recommendation
    assert dossier_row["schema_version"] == "3.0"
    assert dossier_row["document"]["schema_version"] == "3.0"
    assert dossier_row["document"]["report_sha256"] == report_row["sha256"]


def test_deterministic_score_conflict_report_and_dossier_commit_before_completed(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, objects, finding_rows = _audited_run(database, runtime_engine, tenant_records, monkeypatch)
    completion = SupervisorCompletionApplication(session_factory(runtime_engine), objects)
    preparation = completion.prepare_scoring(actor, tenant_records["run_id"])
    _activate_agent(database, tenant_records["run_id"], "evaluation-manager@4.0")
    synthesis = _synthesis(preparation, tenant_records["run_id"], finding_rows)
    result = completion.commit_synthesis_report(
        actor, tenant_records["run_id"], preparation.synthesis_task_id, synthesis
    )
    assert result.decision_conflict is True and result.recommendation == preparation.score.recommendation
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        run = session.execute(select(evaluation_run).where(evaluation_run.c.id == tenant_records["run_id"])).one()
        decision_row = session.execute(select(decision).where(decision.c.id == result.decision_id)).mappings().one()
        report_row = session.execute(select(report).where(report.c.id == result.report_id)).mappings().one()
        dossier_row = (
            session.execute(
                select(project_dossier_snapshot).where(project_dossier_snapshot.c.id == result.dossier_snapshot_id)
            )
            .mappings()
            .one()
        )
        synthesis_row = (
            session.execute(select(manager_synthesis).where(manager_synthesis.c.run_id == tenant_records["run_id"]))
            .mappings()
            .one()
        )
    assert run.status == "COMPLETED" and run.current_stage == "COMPLETED"
    assert decision_row["recommendation"] == preparation.score.recommendation
    assert report_row["status"] == "COMMITTED" and report_row["sha256"] == objects.head(report_row["object_key"]).sha256
    assert dossier_row["decision_id"] == result.decision_id and dossier_row["report_id"] == result.report_id
    assert synthesis_row["status"] == "DECISION_CONFLICT"


def test_nonexistent_synthesis_citation_rejects_report_and_leaves_run_incomplete(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    actor, objects, finding_rows = _audited_run(database, runtime_engine, tenant_records, monkeypatch)
    completion = SupervisorCompletionApplication(session_factory(runtime_engine), objects)
    preparation = completion.prepare_scoring(actor, tenant_records["run_id"])
    _activate_agent(database, tenant_records["run_id"], "evaluation-manager@4.0")
    synthesis = _synthesis(preparation, tenant_records["run_id"], finding_rows, nonexistent_citation=True)
    with pytest.raises(CompletionValidationError, match="nonexistent Finding"):
        completion.commit_synthesis_report(actor, tenant_records["run_id"], preparation.synthesis_task_id, synthesis)
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        run = session.execute(select(evaluation_run).where(evaluation_run.c.id == tenant_records["run_id"])).one()
        report_count = session.execute(select(report.c.id).where(report.c.run_id == tenant_records["run_id"])).all()
        dossier_count = session.execute(
            select(project_dossier_snapshot.c.id).where(project_dossier_snapshot.c.run_id == tenant_records["run_id"])
        ).all()
    assert run.status == "RUNNING" and run.current_stage == "SUPERVISOR_SYNTHESIS"
    assert report_count == [] and dossier_count == []
