"""Generation-v4 domain-result ingestion, serial audit, and bounded remediation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    agent_plan,
    agent_report_artifact,
    agent_task_ticket,
    evaluation_run,
    evidence,
    evidence_audit,
    evidence_source_locator,
    finding,
    project,
    run_manifest,
    skill_version,
    stage,
    task,
    task_material_scope,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.modules.evaluation.intake_application import IntakeValidationError
from launchscope_api.modules.evaluation.task_dispatch import enqueue_ready_tasks
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_domain.value_objects import TenantScope

from .intake_application import supervisor_1p4_enabled
from .specialist_report_v3 import SpecialistReportV3Adapter

_ROOT = Path(__file__).resolve().parents[6]
_DOMAIN_AGENTS = frozenset({"user-evidence", "product-engineering", "business-investment"})
_TERMINAL = frozenset({"SUCCEEDED", "KNOWN_FAILED", "FAILED"})
_FAIL_CLOSED_CLASSES = frozenset(
    {"SUBMISSION_UNKNOWN", "BILLING_UNKNOWN", "UNKNOWN_BILLING", "UNKNOWN_COST", "PAID_CALL_TIMEOUT"}
)
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


class ContentObjectStore(Protocol):
    def head(self, object_key: str) -> Any: ...

    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str: ...

    def get_private(self, object_key: str, *, max_bytes: int = 2_000_000) -> bytes: ...


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _normalize_specialist_report_document(document: dict[str, Any], *, product_title: str) -> dict[str, Any]:
    legacy_metadata = {
        "tenant_id",
        "task_id",
        "standard_version",
        "generated_at",
        "as_of",
        "region_scope",
        "product_version",
    }
    return {
        **{key: value for key, value in document.items() if key not in legacy_metadata},
        "product_version_id": str(document.get("product_version_id") or document.get("product_version") or ""),
        "product_title": str(document.get("product_title") or product_title),
    }


def _apply_canonical_evidence_refs(document: dict[str, Any], canonical_by_ref_or_sha: dict[str, str]) -> None:
    submitted_to_canonical: dict[str, str] = {}
    for reference in document["evidence_refs"]:
        digest = str(reference["sha256"])
        submitted = str(reference["ref"])
        canonical = canonical_by_ref_or_sha.get(submitted) or canonical_by_ref_or_sha.get(digest)
        if canonical is None:
            raise SupervisorWorkflowError("content ref is missing from the task evidence ledger")
        submitted_to_canonical[submitted] = canonical
        reference["ref"] = canonical
    report_ref = document["report_ref"]
    report_canonical = canonical_by_ref_or_sha.get(str(report_ref["ref"])) or canonical_by_ref_or_sha.get(
        str(report_ref["sha256"])
    )
    if report_canonical is None or str(report_ref["ref"]) not in {
        report_canonical,
        *submitted_to_canonical,
    }:
        raise SupervisorWorkflowError("report_ref does not match a SHA-bound task evidence ref")
    report_ref["ref"] = report_canonical
    canonical_values = set(submitted_to_canonical.values())
    for source in document["findings"]:
        source["evidence_refs"] = [
            submitted_to_canonical.get(str(reference), str(reference)) for reference in source["evidence_refs"]
        ]
        if not set(source["evidence_refs"]).issubset(canonical_values):
            raise SupervisorWorkflowError("a finding cites evidence outside the SHA-bound handoff refs")


class SupervisorWorkflowError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DomainIngestionResult:
    task_id: UUID
    state: str
    finding_ids: tuple[UUID, ...]
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class AuditRoundResult:
    audit_round: int
    state: str
    remediation_task_ids: tuple[UUID, ...]
    duplicate: bool = False


class SupervisorAuditApplication:
    def __init__(self, sessions: sessionmaker[Session], objects: ContentObjectStore) -> None:
        self._sessions = sessions
        self._objects = objects
        self._handoff_schema_v3 = json.loads(
            (_ROOT / "packages/contracts/handoffs/agent-handoff.v3.json").read_text(encoding="utf-8")
        )
        self._handoff_schema_v4 = json.loads(
            (_ROOT / "packages/contracts/handoffs/agent-handoff.v4.json").read_text(encoding="utf-8")
        )
        self._audit_schema_v3 = json.loads(
            (_ROOT / "packages/contracts/audit/audit-result.v3.json").read_text(encoding="utf-8")
        )
        self._audit_schema_v4 = json.loads(
            (_ROOT / "packages/contracts/audit/audit-result.v4.json").read_text(encoding="utf-8")
        )
        self._specialist_report_schema = json.loads(
            (_ROOT / "packages/contracts/reports/specialist-report.v2.json").read_text(encoding="utf-8")
        )
        self._specialist_report_schema_v3 = json.loads(
            (_ROOT / "packages/contracts/reports/specialist-report.v3.json").read_text(encoding="utf-8")
        )
        self._specialist_report_v3 = SpecialistReportV3Adapter()

    def _domain_handoff_contract(self, document: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if document.get("schema_version") == "4.0":
            return self._handoff_schema_v4, "AgentHandoffV4"
        return self._handoff_schema_v3, "AgentHandoffV3"

    def ingest_domain_handoff(
        self,
        actor: Actor,
        run_id: UUID,
        task_id: UUID,
        document: dict[str, Any],
        *,
        specialist_report: dict[str, Any] | None = None,
    ) -> DomainIngestionResult:
        self._require_enabled()
        handoff_schema, handoff_name = self._domain_handoff_contract(document)
        self._validate(handoff_schema, document, handoff_name)
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            assigned = (
                session.execute(
                    select(task)
                    .where(
                        task.c.tenant_id == actor.tenant_id,
                        task.c.id == task_id,
                        task.c.run_id == run_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if assigned is None:
                raise NotFoundError("domain task was not found")
            agent = str(assigned["agent_identity_ref"]).split("@", 1)[0]
            if (
                agent not in _DOMAIN_AGENTS
                or assigned["agent_identity_ref"] not in {f"{agent}@4.0", f"{agent}@5.0", f"{agent}@6.0"}
                or UUID(str(document["run_id"])) != run_id
                or UUID(str(document["task_id"])) != task_id
                or document["agent_code"] != agent
                or int(document["dispatch_epoch"]) != int(assigned["dispatch_epoch"])
            ):
                raise SupervisorWorkflowError("handoff routing does not match the assigned generation-v4 task")
            existing_ids = tuple(
                session.execute(
                    select(finding.c.id).where(
                        finding.c.tenant_id == actor.tenant_id,
                        finding.c.task_id == task_id,
                    )
                ).scalars()
            )
            if assigned["status"] == "SUCCEEDED" and existing_ids:
                return DomainIngestionResult(task_id, "DOMAIN_REVIEW", existing_ids, duplicate=True)
            if assigned["status"] not in {"PENDING", "RUNNING"}:
                raise SupervisorWorkflowError("a terminal or attention task cannot accept another handoff")
            status = str(document["status"])
            failure_class = document["failure_class"]
            if status == "SUBMISSION_UNKNOWN" or failure_class in _FAIL_CLOSED_CLASSES:
                self._fail_closed(session, actor.tenant_id, run_id, task_id, str(failure_class or status), now)
                return DomainIngestionResult(task_id, "NEEDS_ATTENTION", ())
            if status == "KNOWN_FAILED":
                if not failure_class:
                    raise SupervisorWorkflowError("known failures require an explicit failure_class")
                session.execute(
                    update(task)
                    .where(task.c.tenant_id == actor.tenant_id, task.c.id == task_id)
                    .values(
                        status="KNOWN_FAILED",
                        last_failure_class=str(failure_class),
                        last_error=str(document["next_action"]),
                        updated_at=now,
                    )
                )
                state = self._materialize_audit_if_ready(
                    session,
                    actor.tenant_id,
                    run_id,
                    now,
                    audit_round=1,
                )
                return DomainIngestionResult(task_id, state, ())
            if status == "NEEDS_INPUT":
                session.execute(
                    update(task)
                    .where(task.c.tenant_id == actor.tenant_id, task.c.id == task_id)
                    .values(status="WAITING_FOR_USER", updated_at=now)
                )
                session.execute(
                    update(evaluation_run)
                    .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                    .values(status="WAITING_FOR_USER", current_stage="WAITING_FOR_USER", updated_at=now)
                )
                return DomainIngestionResult(task_id, "WAITING_FOR_USER", ())
            if str(assigned["agent_identity_ref"]).endswith("@6.0"):
                required_material_ids = set(
                    session.execute(
                        select(task_material_scope.c.material_id).where(
                            task_material_scope.c.tenant_id == actor.tenant_id,
                            task_material_scope.c.run_id == run_id,
                            task_material_scope.c.task_id == task_id,
                            task_material_scope.c.required.is_(True),
                        )
                    ).scalars()
                )
                cited_material_ids = set(
                    session.execute(
                        select(evidence.c.material_id).where(
                            evidence.c.tenant_id == actor.tenant_id,
                            evidence.c.run_id == run_id,
                            evidence.c.task_id == task_id,
                            evidence.c.source_type == "MATERIAL_UNIT",
                        )
                    ).scalars()
                )
                missing_material_ids = required_material_ids - cited_material_ids
                if missing_material_ids:
                    session.execute(
                        update(task)
                        .where(task.c.tenant_id == actor.tenant_id, task.c.id == task_id)
                        .values(
                            status="KNOWN_FAILED",
                            last_failure_class="REQUIRED_MATERIAL_NOT_READ",
                            last_error=(
                                f"Read at least one assigned unit from each of the "
                                f"{len(missing_material_ids)} missing required materials before resubmission."
                            ),
                            updated_at=now,
                        )
                    )
                    state = self._materialize_audit_if_ready(
                        session,
                        actor.tenant_id,
                        run_id,
                        now,
                        audit_round=1,
                    )
                    return DomainIngestionResult(task_id, state, ())
                if specialist_report is None and not isinstance(document.get("report_ref"), dict):
                    raise SupervisorWorkflowError(
                        "report v2.2 domain work requires one SpecialistReportDocumentV2 body or existing ref"
                    )
                if specialist_report is not None:
                    staged_report_ref = self._persist_specialist_report(
                        session,
                        actor.tenant_id,
                        run_id,
                        task_id,
                        agent,
                        specialist_report,
                    )
                    evidence_refs = list(document["evidence_refs"])
                    if staged_report_ref not in evidence_refs:
                        evidence_refs.append(staged_report_ref)
                    document = {**document, "report_ref": staged_report_ref, "evidence_refs": evidence_refs}
                    self._validate(handoff_schema, document, handoff_name)
            findings = document["findings"]
            report_ref = document["report_ref"]
            if not findings or report_ref is None:
                raise SupervisorWorkflowError("successful domain work requires findings and an immutable report_ref")
            remediation_target = self._remediation_target(session, actor.tenant_id, task_id)
            submitted_finding_ids = [UUID(str(item["finding_id"])) for item in findings]
            duplicate_finding_id = session.execute(
                select(finding.c.id)
                .where(
                    finding.c.tenant_id == actor.tenant_id,
                    finding.c.id.in_(submitted_finding_ids),
                )
                .limit(1)
            ).scalar_one_or_none()
            if duplicate_finding_id is not None:
                if remediation_target is not None:
                    raise SupervisorWorkflowError(
                        "targeted remediation must emit a new finding_id instead of reusing an immutable Finding"
                    )
                raise SupervisorWorkflowError("finding_id is already present in the immutable Finding ledger")
            canonical_by_ref_or_sha: dict[str, str] = {}
            for item in document["evidence_refs"]:
                submitted_ref = str(item["ref"])
                submitted_sha = str(item["sha256"])
                keys = tuple(
                    session.execute(
                        select(evidence.c.object_key)
                        .where(
                            evidence.c.tenant_id == actor.tenant_id,
                            evidence.c.run_id == run_id,
                            evidence.c.task_id == task_id,
                            evidence.c.object_key == submitted_ref,
                            evidence.c.sha256 == submitted_sha,
                        )
                        .distinct()
                    ).scalars()
                )
                if not keys:
                    keys = tuple(
                        session.execute(
                            select(evidence.c.object_key)
                            .where(
                                evidence.c.tenant_id == actor.tenant_id,
                                evidence.c.run_id == run_id,
                                evidence.c.task_id == task_id,
                                evidence.c.sha256 == submitted_sha,
                            )
                            .distinct()
                        ).scalars()
                    )
                if len(keys) != 1:
                    raise SupervisorWorkflowError("content ref is missing or ambiguous in the task evidence ledger")
                canonical_by_ref_or_sha[submitted_ref] = str(keys[0])
            _apply_canonical_evidence_refs(document, canonical_by_ref_or_sha)
            self._verify_content_ref(report_ref)
            evidence_by_ref = {str(item["ref"]): item for item in document["evidence_refs"]}
            for item in evidence_by_ref.values():
                self._verify_content_ref(item)
            report_mime_type = session.execute(
                select(evidence.c.mime_type).where(
                    evidence.c.tenant_id == actor.tenant_id,
                    evidence.c.run_id == run_id,
                    evidence.c.task_id == task_id,
                    evidence.c.object_key == str(report_ref["ref"]),
                    evidence.c.sha256 == str(report_ref["sha256"]),
                )
            ).scalar_one_or_none()
            if report_mime_type is None:
                raise SupervisorWorkflowError("domain report is missing from the immutable evidence ledger")
            report_id = uuid4()
            report_key = str(report_ref["ref"])
            report_sha = str(report_ref["sha256"])
            if str(assigned["agent_identity_ref"]).endswith("@6.0"):
                try:
                    report_body = self._objects.get_private(report_key, max_bytes=2_097_152)
                    if hashlib.sha256(report_body).hexdigest() != report_sha:
                        raise SupervisorWorkflowError("specialist report body differs from its immutable reference")
                    report_document = json.loads(report_body)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise SupervisorWorkflowError("specialist report body must be one JSON document") from exc
                identity = (
                    session.execute(
                        select(
                            evaluation_run.c.project_id,
                            evaluation_run.c.product_version_id,
                            evaluation_run.c.state_flags,
                            project.c.name,
                        )
                        .select_from(
                            evaluation_run.join(
                                project,
                                (project.c.tenant_id == evaluation_run.c.tenant_id)
                                & (project.c.id == evaluation_run.c.project_id),
                            )
                        )
                        .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                    )
                    .mappings()
                    .one()
                )
                report_v3 = (identity["state_flags"] or {}).get("architecture_generation") == (
                    "supervisor-1p4-report-v3"
                )
                if report_v3 and report_document.get("schema_version") == "3.0":
                    self._validate(self._specialist_report_schema_v3, report_document, "SpecialistReportDocumentV3")
                else:
                    self._validate(self._specialist_report_schema, report_document, "SpecialistReportDocumentV2")
                if (
                    report_document["run_id"] != str(run_id)
                    or report_document["project_id"] != str(identity["project_id"])
                    or report_document["product_version_id"] != str(identity["product_version_id"])
                    or report_document["product_title"] != str(identity["name"])
                    or report_document["agent_code"] != agent
                ):
                    raise SupervisorWorkflowError("specialist report identity does not match the assigned Run")
                if report_v3 and report_document.get("schema_version") == "2.0":
                    persisted = self._persist_specialist_report(
                        session,
                        actor.tenant_id,
                        run_id,
                        task_id,
                        agent,
                        report_document,
                    )
                    report_key = persisted["ref"]
                    report_sha = persisted["sha256"]
                report_id = UUID(str(report_document["report_id"]))
            else:
                report_document = {
                    "schema_version": "DomainAgentReportViewV1",
                    "run_id": str(run_id),
                    "task_id": str(task_id),
                    "agent_code": agent,
                    "dispatch_epoch": int(document["dispatch_epoch"]),
                    "status": "AVAILABLE",
                    "confidence": document["confidence"],
                    "findings": findings,
                    "limitations": document["limitations"],
                    "next_action": document["next_action"],
                    "evidence_refs": document["evidence_refs"],
                    "submitted_report_ref": {**report_ref, "mime_type": str(report_mime_type)},
                }
                report_body = _canonical(report_document)
                report_sha = hashlib.sha256(report_body).hexdigest()
                report_key = (
                    f"tenant/{actor.tenant_id}/run/{run_id}/agent-reports/"
                    f"{agent}/revision-{int(document['dispatch_epoch'])}-{report_sha}.json"
                )
                try:
                    stored_sha = self._objects.put_private(report_key, report_body, "application/json")
                    metadata = self._objects.head(report_key)
                    if stored_sha != report_sha or metadata is None or metadata.sha256 != report_sha:
                        raise SupervisorWorkflowError("domain report projection could not be reconciled")
                except Exception:
                    self._fail_closed(session, actor.tenant_id, run_id, task_id, "REPORT_PERSISTENCE_UNKNOWN", now)
                    return DomainIngestionResult(task_id, "NEEDS_ATTENTION", ())
            finding_ids: list[UUID] = []
            for source in findings:
                if not set(source["evidence_refs"]).issubset(evidence_by_ref):
                    raise SupervisorWorkflowError("a finding cites evidence outside the SHA-bound handoff refs")
                finding_id = UUID(str(source["finding_id"]))
                session.execute(
                    finding.insert().values(
                        id=finding_id,
                        tenant_id=actor.tenant_id,
                        run_id=run_id,
                        task_id=task_id,
                        dimension_code=str(source["dimension"]),
                        grade=str(source["grade"]),
                        claim_type="FINDING",
                        statement=str(source["claim"]),
                        is_hypothesis=bool(source["hypothesis"]),
                        submitted_by=agent,
                        submitted_at=now,
                        supersedes_id=remediation_target,
                        structured_result={"finding": source, "report_ref": report_ref},
                        simulated=False,
                        hard_block=False,
                        block_reason=None,
                    )
                )
                finding_ids.append(finding_id)
            session.execute(
                agent_report_artifact.insert().values(
                    id=report_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    agent_code=agent,
                    report_kind="DOMAIN",
                    revision=int(document["dispatch_epoch"]),
                    object_key=report_key,
                    sha256=report_sha,
                    mime_type="application/json",
                    status="AVAILABLE",
                    created_at=now,
                )
            )
            session.execute(
                update(task)
                .where(task.c.tenant_id == actor.tenant_id, task.c.id == task_id)
                .values(status="SUCCEEDED", updated_at=now)
            )
            audit_round = 2 if assigned["stage_code"] == "TARGETED_REMEDIATION" else 1
            state = self._materialize_audit_if_ready(session, actor.tenant_id, run_id, now, audit_round=audit_round)
            return DomainIngestionResult(task_id, state, tuple(finding_ids))

    def reconcile_domain_completion(self, actor: Actor, run_id: UUID) -> str:
        self._require_enabled()
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            run = (
                session.execute(
                    select(
                        evaluation_run.c.status,
                        evaluation_run.c.current_stage,
                        evaluation_run.c.attention_reason,
                    ).where(
                        evaluation_run.c.tenant_id == actor.tenant_id,
                        evaluation_run.c.id == run_id,
                    )
                )
                .mappings()
                .first()
            )
            if run is None:
                raise NotFoundError("evaluation run was not found")
            if run["current_stage"] not in {"DOMAIN_REVIEW", "TARGETED_REMEDIATION"}:
                return str(run["current_stage"] or run["status"])
            if run["status"] == "NEEDS_ATTENTION" and run["attention_reason"] != "REQUIRED_AGENT_FAILED":
                return str(run["current_stage"] or run["status"])
            if run["status"] not in {"RUNNING", "NEEDS_ATTENTION"}:
                return str(run["current_stage"] or run["status"])
            if run["current_stage"] == "TARGETED_REMEDIATION":
                pending_ids = tuple(
                    session.execute(
                        select(task.c.id).where(
                            task.c.tenant_id == actor.tenant_id,
                            task.c.run_id == run_id,
                            task.c.stage_code == "TARGETED_REMEDIATION",
                            task.c.status == "PENDING",
                        )
                    ).scalars()
                )
                if pending_ids:
                    session.execute(
                        update(task)
                        .where(task.c.tenant_id == actor.tenant_id, task.c.id.in_(pending_ids))
                        .values(status="READY", updated_at=now)
                    )
                    enqueued = enqueue_ready_tasks(session, actor.tenant_id, run_id, "TARGETED_REMEDIATION")
                    if enqueued != len(pending_ids):
                        raise SupervisorWorkflowError("every targeted remediation Task must be enqueued")
                    return "TARGETED_REMEDIATION"
                return self._materialize_audit_if_ready(session, actor.tenant_id, run_id, now, audit_round=2)
            return self._materialize_audit_if_ready(session, actor.tenant_id, run_id, now, audit_round=1)

    def _materialize_audit_if_ready(
        self, session: Session, tenant_id: UUID, run_id: UUID, now: datetime, *, audit_round: int = 1
    ) -> str:
        source_stage = "DOMAIN_REVIEW" if audit_round == 1 else "TARGETED_REMEDIATION"
        domain_rows = (
            session.execute(
                select(
                    task.c.id,
                    task.c.status,
                    task.c.required,
                    task.c.last_failure_class,
                    task.c.agent_identity_ref,
                ).where(
                    task.c.tenant_id == tenant_id,
                    task.c.run_id == run_id,
                    task.c.stage_code == source_stage,
                    task.c.agent_identity_ref.in_(
                        [f"{agent}@{version}" for agent in sorted(_DOMAIN_AGENTS) for version in ("4.0", "5.0", "6.0")]
                    ),
                )
            )
            .mappings()
            .all()
        )
        required_count = len(_DOMAIN_AGENTS) if audit_round == 1 else 1
        if len(domain_rows) < required_count or any(row["status"] not in _TERMINAL for row in domain_rows):
            return source_stage
        required_failure = next(
            (row for row in domain_rows if row["required"] and row["status"] in {"KNOWN_FAILED", "FAILED"}),
            None,
        )
        if required_failure is not None:
            session.execute(
                update(evaluation_run)
                .where(evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id)
                .values(
                    status="NEEDS_ATTENTION",
                    current_stage="NEEDS_ATTENTION",
                    last_failure_class=required_failure["last_failure_class"],
                    attention_reason="REQUIRED_AGENT_FAILED",
                    updated_at=now,
                )
            )
            return "NEEDS_ATTENTION"
        agent_version = (
            "6.0"
            if any(str(row["agent_identity_ref"]).endswith("@6.0") for row in domain_rows)
            else "5.0"
            if any(str(row["agent_identity_ref"]).endswith("@5.0") for row in domain_rows)
            else "4.0"
        )
        context_tool = "launchscope-context.get.v2" if agent_version in {"5.0", "6.0"} else "launchscope-context.get.v1"
        session.execute(
            update(stage)
            .where(
                stage.c.tenant_id == tenant_id,
                stage.c.run_id == run_id,
                stage.c.code == source_stage,
                stage.c.status != "COMPLETED",
            )
            .values(status="COMPLETED", completed_at=now)
        )
        existing = session.execute(
            select(task.c.id).where(
                task.c.tenant_id == tenant_id,
                task.c.run_id == run_id,
                task.c.stage_code == "EVIDENCE_AUDIT",
                task.c.agent_identity_ref == f"evidence-auditor@{agent_version}",
                task.c.dispatch_epoch == audit_round - 1,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return "EVIDENCE_AUDIT"
        selected_audit_skill_version = "2.2.0" if agent_version == "6.0" else "2.1.0"
        audit_skill_version_id = session.execute(
            select(skill_version.c.id).where(
                skill_version.c.skill_code == "evidence-grounding-audit",
                skill_version.c.version == selected_audit_skill_version,
            )
        ).scalar_one_or_none()
        if audit_skill_version_id is None:
            raise SupervisorWorkflowError(
                f"registered Skill version is missing: evidence-grounding-audit@{selected_audit_skill_version}"
            )
        plan = (
            session.execute(
                select(agent_plan.c.id, agent_plan.c.plan_version).where(
                    agent_plan.c.tenant_id == tenant_id,
                    agent_plan.c.run_id == run_id,
                    agent_plan.c.status == "ACCEPTED",
                )
            )
            .mappings()
            .one()
        )
        finding_ids = [
            str(item)
            for item in session.execute(
                select(finding.c.id)
                .where(finding.c.tenant_id == tenant_id, finding.c.run_id == run_id)
                .order_by(finding.c.submitted_at, finding.c.id)
            ).scalars()
        ]
        if not finding_ids:
            raise SupervisorWorkflowError("evidence audit requires at least one immutable Finding")
        task_id, ticket_id = uuid4(), uuid4()
        deadline = now + timedelta(seconds=3600)
        stage_id = session.execute(
            select(stage.c.id).where(
                stage.c.tenant_id == tenant_id,
                stage.c.run_id == run_id,
                stage.c.code == "EVIDENCE_AUDIT",
            )
        ).scalar_one_or_none()
        if stage_id is None:
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
                    code="EVIDENCE_AUDIT",
                    ordinal=ordinal,
                    status="READY",
                    started_at=None,
                    completed_at=None,
                )
            )
        public_summary = {
            "schema_version": "3.0",
            "ticket_id": str(ticket_id),
            "run_id": str(run_id),
            "task_id": str(task_id),
            "plan_id": str(plan["id"]),
            "plan_version": int(plan["plan_version"]),
            "dispatch_epoch": audit_round - 1,
            "target_agent": "evidence-auditor",
            "objective": (
                f"perform evidence audit round {audit_round} for every immutable domain Finding "
                "against its SHA-bound evidence"
            ),
            "input_refs": [f"finding:{finding_id}" for finding_id in finding_ids],
            "analysis_dimensions": ["source", "scope", "freshness", "conflict", "authorization"],
            "region_scope": ["as-submitted"],
            "as_of": now.date().isoformat(),
            "tool_policy": [context_tool, *(["material.read.v1"] if agent_version in {"5.0", "6.0"} else [])],
            "success_conditions": [
                "one valid AuditResultV4 for every available Finding",
                f"every AuditResultV4 document sets audit_round to {audit_round}",
            ],
            "required": True,
            "deadline_at": deadline.isoformat().replace("+00:00", "Z"),
            "report_contract": "specialist-report.v2" if agent_version == "6.0" else "audit-result.v4",
            "handoff_contract": "audit-result.v4",
        }
        session.execute(
            task.insert().values(
                id=task_id,
                tenant_id=tenant_id,
                run_id=run_id,
                stage_id=stage_id,
                agent_identity_id=None,
                skill_version_id=audit_skill_version_id,
                stage_code="EVIDENCE_AUDIT",
                agent_identity_ref=f"evidence-auditor@{agent_version}",
                skill_ref="evidence-grounding-audit",
                skill_version=selected_audit_skill_version,
                status="READY",
                lease_token=None,
                idempotency_key=f"v4-audit:{run_id}:{audit_round}",
                dependencies=[str(item["id"]) for item in domain_rows],
                tool_allowlist=public_summary["tool_policy"],
                budget_slice={"suggested_usd": 0},
                timeout_seconds=3600,
                success_condition=public_summary["success_conditions"],
                evidence_requirement="audit every Finding and preserve exact source hashes and evidence refs",
                required=True,
                correction_attempts=0,
                transient_retries=0,
                dispatch_epoch=audit_round - 1,
                last_failure_class=None,
                last_error=None,
                side_effect_started=False,
                created_at=now,
                updated_at=now,
            )
        )
        ticket_sha = hashlib.sha256(_canonical(public_summary)).hexdigest()
        session.execute(
            agent_task_ticket.insert().values(
                id=ticket_id,
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=task_id,
                plan_id=plan["id"],
                dispatch_epoch=audit_round - 1,
                target_agent="evidence-auditor",
                ticket_sha256=ticket_sha,
                public_summary=public_summary,
                usage_baseline=None,
                status="PREPARED",
                expires_at=deadline,
                created_at=now,
                delivered_at=None,
            )
        )
        session.execute(
            update(evaluation_run)
            .where(evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id)
            .values(status="RUNNING", current_stage="EVIDENCE_AUDIT", updated_at=now)
        )
        if enqueue_ready_tasks(session, tenant_id, run_id, "EVIDENCE_AUDIT") != 1:
            raise SupervisorWorkflowError("generation-v4 audit must enqueue exactly one evidence-auditor task")
        return "EVIDENCE_AUDIT"

    def submit_audit_results(
        self,
        actor: Actor,
        run_id: UUID,
        documents: list[dict[str, Any]],
        *,
        task_id: UUID | None = None,
        specialist_report_ref: dict[str, Any] | None = None,
        specialist_report: dict[str, Any] | None = None,
    ) -> AuditRoundResult:
        self._require_enabled()
        if not documents:
            raise SupervisorWorkflowError("audit results cannot be empty")
        rounds = {int(item["audit_round"]) for item in documents}
        if len(rounds) != 1:
            raise SupervisorWorkflowError("one submission must contain exactly one audit round")
        audit_round = rounds.pop()
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
            frozen_config = session.execute(
                select(run_manifest.c.frozen_config).where(
                    run_manifest.c.tenant_id == actor.tenant_id,
                    run_manifest.c.run_id == run_id,
                )
            ).scalar_one()
            report_v2_run = (frozen_config or {}).get("agent_contract_generation") == "v6"
            submitted_schema = self._audit_schema_v4 if report_v2_run else self._audit_schema_v3
            submitted_contract = "AuditResultV4" if report_v2_run else "AuditResultV3"
            for document in documents:
                self._validate(submitted_schema, document, submitted_contract)
            existing_audit_ids = set(
                session.execute(
                    select(evidence_audit.c.id).where(
                        evidence_audit.c.tenant_id == actor.tenant_id,
                        evidence_audit.c.id.in_([UUID(str(item["audit_id"])) for item in documents]),
                    )
                ).scalars()
            )
            if len(existing_audit_ids) == len(documents):
                return AuditRoundResult(audit_round, str(run["current_stage"]), (), duplicate=True)
            if existing_audit_ids:
                raise SupervisorWorkflowError("partial audit replay is not allowed")
            if self._assert_domain_terminal(session, actor.tenant_id, run_id, audit_round, now):
                return AuditRoundResult(audit_round, "NEEDS_ATTENTION", ())
            prior_round = session.execute(
                select(func.coalesce(func.max(evidence_audit.c.audit_round), 0)).where(
                    evidence_audit.c.tenant_id == actor.tenant_id,
                    evidence_audit.c.run_id == run_id,
                )
            ).scalar_one()
            if audit_round != int(prior_round) + 1 or audit_round > 2:
                raise SupervisorWorkflowError("audit and re-audit must execute serially and at most once each")
            finding_rows = (
                session.execute(
                    select(finding).where(finding.c.tenant_id == actor.tenant_id, finding.c.run_id == run_id)
                )
                .mappings()
                .all()
            )
            by_id = {row["id"]: row for row in finding_rows}
            submitted_ids = {UUID(str(item["finding_id"])) for item in documents}
            if not by_id or submitted_ids != set(by_id):
                raise SupervisorWorkflowError("the audit must cover every and only available immutable Finding")
            if task_id is None:
                active_auditors = tuple(
                    session.execute(
                        select(task.c.id).where(
                            task.c.tenant_id == actor.tenant_id,
                            task.c.run_id == run_id,
                            task.c.agent_identity_ref.in_(
                                ("evidence-auditor@4.0", "evidence-auditor@5.0", "evidence-auditor@6.0")
                            ),
                            task.c.status == "RUNNING",
                        )
                    ).scalars()
                )
                if len(active_auditors) != 1:
                    raise SupervisorWorkflowError("generation-v4 audit reports require one assigned auditor task")
                task_id = active_auditors[0]
            active_auditor = session.execute(
                select(task.c.id).where(
                    task.c.tenant_id == actor.tenant_id,
                    task.c.run_id == run_id,
                    task.c.id == task_id,
                    task.c.agent_identity_ref.in_(
                        ("evidence-auditor@4.0", "evidence-auditor@5.0", "evidence-auditor@6.0")
                    ),
                    task.c.status == "RUNNING",
                )
            ).scalar_one_or_none()
            if active_auditor is None:
                raise SupervisorWorkflowError("the assigned evidence-auditor task is not active")
            auditor_ref = session.execute(
                select(task.c.agent_identity_ref).where(
                    task.c.tenant_id == actor.tenant_id,
                    task.c.id == task_id,
                )
            ).scalar_one()
            needs_more: list[dict[str, Any]] = []
            validated_documents: list[tuple[dict[str, Any], UUID, dict[str, Any], str]] = []
            for document in documents:
                finding_id = UUID(str(document["finding_id"]))
                source = by_id[finding_id]["structured_result"]["finding"]
                source_sha = hashlib.sha256(_canonical(source)).hexdigest()
                if source_sha != document["source_finding_sha256"]:
                    raise SupervisorWorkflowError("audit source hash does not match the immutable Finding")
                document = self._apply_citation_admission(
                    session,
                    actor.tenant_id,
                    run_id,
                    source,
                    document,
                    now,
                )
                self._validate(self._audit_schema_v4, document, "AuditResultV4")
                if document["decision"] == "NEEDS_MORE":
                    if audit_round == 2 or document["remediation_target"] is None:
                        raise SupervisorWorkflowError("NEEDS_MORE is allowed only in the first audit with a target")
                    needs_more.append(document)
                elif document["remediation_target"] is not None:
                    raise SupervisorWorkflowError("only NEEDS_MORE may carry a remediation target")
                validated_documents.append((document, finding_id, source, source_sha))
            audit_report_id = uuid4()
            if str(auditor_ref).endswith("@6.0"):
                if specialist_report is not None:
                    if specialist_report_ref is not None:
                        raise SupervisorWorkflowError(
                            "report v2.2 audit accepts either a report body or an existing ref, not both"
                        )
                    specialist_report_ref = self._persist_specialist_report(
                        session,
                        actor.tenant_id,
                        run_id,
                        task_id,
                        "evidence-auditor",
                        specialist_report,
                    )
                if not isinstance(specialist_report_ref, dict):
                    raise SupervisorWorkflowError("report v2.2 audit requires one SpecialistReportDocumentV2 ref")
                audit_key = str(specialist_report_ref.get("ref", ""))
                audit_sha = str(specialist_report_ref.get("sha256", ""))
                registered = session.execute(
                    select(evidence.c.id).where(
                        evidence.c.tenant_id == actor.tenant_id,
                        evidence.c.run_id == run_id,
                        evidence.c.task_id == task_id,
                        evidence.c.object_key == audit_key,
                        evidence.c.sha256 == audit_sha,
                    )
                ).scalar_one_or_none()
                if registered is None:
                    raise SupervisorWorkflowError("audit specialist report is outside the task evidence ledger")
                try:
                    audit_body = self._objects.get_private(audit_key, max_bytes=2_097_152)
                    if hashlib.sha256(audit_body).hexdigest() != audit_sha:
                        raise SupervisorWorkflowError("audit specialist report differs from its immutable reference")
                    audit_report = json.loads(audit_body)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise SupervisorWorkflowError("audit specialist report must be one JSON document") from exc
                identity = (
                    session.execute(
                        select(
                            evaluation_run.c.project_id,
                            evaluation_run.c.product_version_id,
                            evaluation_run.c.state_flags,
                            project.c.name,
                        )
                        .select_from(
                            evaluation_run.join(
                                project,
                                (project.c.tenant_id == evaluation_run.c.tenant_id)
                                & (project.c.id == evaluation_run.c.project_id),
                            )
                        )
                        .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                    )
                    .mappings()
                    .one()
                )
                if (identity["state_flags"] or {}).get("architecture_generation") == "supervisor-1p4-report-v3":
                    self._validate(
                        self._specialist_report_schema_v3,
                        audit_report,
                        "SpecialistReportDocumentV3",
                    )
                else:
                    self._validate(self._specialist_report_schema, audit_report, "SpecialistReportDocumentV2")
                if (
                    audit_report["run_id"] != str(run_id)
                    or audit_report["project_id"] != str(identity["project_id"])
                    or audit_report["product_version_id"] != str(identity["product_version_id"])
                    or audit_report["product_title"] != str(identity["name"])
                    or audit_report["agent_code"] != "evidence-auditor"
                ):
                    raise SupervisorWorkflowError("audit specialist report identity does not match the assigned Run")
                audit_report_id = UUID(str(audit_report["report_id"]))
            else:
                audit_report = {
                    "schema_version": "AgentAuditReportV1",
                    "run_id": str(run_id),
                    "task_id": str(task_id),
                    "agent_code": "evidence-auditor",
                    "audit_round": audit_round,
                    "documents": documents,
                }
                audit_body = _canonical(audit_report)
                audit_sha = hashlib.sha256(audit_body).hexdigest()
                audit_key = (
                    f"tenant/{actor.tenant_id}/run/{run_id}/agent-reports/"
                    f"evidence-auditor/audit-round-{audit_round}-{audit_sha}.json"
                )
                try:
                    stored_sha = self._objects.put_private(audit_key, audit_body, "application/json")
                    metadata = self._objects.head(audit_key)
                    if stored_sha != audit_sha or metadata is None or metadata.sha256 != audit_sha:
                        raise SupervisorWorkflowError("audit report persistence could not be reconciled")
                except Exception:
                    self._fail_closed(session, actor.tenant_id, run_id, task_id, "REPORT_PERSISTENCE_UNKNOWN", now)
                    return AuditRoundResult(audit_round, "NEEDS_ATTENTION", ())
            for document, finding_id, _source, source_sha in validated_documents:
                session.execute(
                    evidence_audit.insert().values(
                        id=UUID(str(document["audit_id"])),
                        tenant_id=actor.tenant_id,
                        run_id=run_id,
                        finding_id=finding_id,
                        decision=document["decision"],
                        auditor_id=auditor_ref,
                        reason=document["reason"],
                        contract_version="4.0",
                        rule_ids=document["rule_ids"],
                        referenced_evidence_ids=document["evidence_ids"],
                        score_components={
                            "support_strength": document["support_strength"],
                            "independent_source_count": document["independent_source_count"],
                            "freshness_status": document["freshness_status"],
                            "freshness_score": document["freshness_score"],
                            "source_locator_ids": document["source_locator_ids"],
                            "citation_status": document["citation_status"],
                            "score_bearing": document["score_bearing"],
                        },
                        flags=[
                            document["freshness_status"],
                            document["citation_status"],
                            *document["conflict_group_ids"],
                        ],
                        source_finding_sha256=source_sha,
                        audit_round=audit_round,
                        remediation_target=document["remediation_target"],
                        audited_at=now,
                    )
                )
            session.execute(
                agent_report_artifact.insert().values(
                    id=audit_report_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    agent_code="evidence-auditor",
                    report_kind="AUDIT",
                    revision=audit_round,
                    object_key=audit_key,
                    sha256=audit_sha,
                    mime_type="application/json",
                    status="AVAILABLE",
                    created_at=now,
                )
            )
            if needs_more:
                remediation_ids = self._materialize_remediation(session, actor.tenant_id, run_id, needs_more, now)
                session.execute(
                    update(task)
                    .where(task.c.tenant_id == actor.tenant_id, task.c.id.in_(remediation_ids))
                    .values(status="READY", updated_at=now)
                )
                enqueued = enqueue_ready_tasks(session, actor.tenant_id, run_id, "TARGETED_REMEDIATION")
                if enqueued != len(remediation_ids):
                    raise SupervisorWorkflowError("every targeted remediation Task must be enqueued")
                state = "TARGETED_REMEDIATION"
            else:
                remediation_ids = []
                state = "DETERMINISTIC_SCORING"
            completed = session.execute(
                update(task)
                .where(
                    task.c.tenant_id == actor.tenant_id,
                    task.c.run_id == run_id,
                    task.c.id == task_id,
                    task.c.agent_identity_ref == auditor_ref,
                    task.c.status == "RUNNING",
                )
                .values(status="SUCCEEDED", updated_at=now)
            )
            if getattr(completed, "rowcount", 0) != 1:
                raise SupervisorWorkflowError("the active evidence-auditor task could not be settled")
            session.execute(
                update(stage)
                .where(
                    stage.c.tenant_id == actor.tenant_id,
                    stage.c.run_id == run_id,
                    stage.c.code == "EVIDENCE_AUDIT",
                )
                .values(status="COMPLETED", completed_at=now)
            )
            session.execute(
                update(evaluation_run)
                .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                .values(status="RUNNING", current_stage=state, updated_at=now)
            )
            return AuditRoundResult(audit_round, state, tuple(remediation_ids))

    def _persist_specialist_report(
        self,
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        task_id: UUID,
        agent_code: str,
        document: dict[str, Any],
    ) -> dict[str, str]:
        identity = (
            session.execute(
                select(
                    evaluation_run.c.project_id,
                    evaluation_run.c.product_version_id,
                    evaluation_run.c.state_flags,
                    project.c.name,
                )
                .select_from(
                    evaluation_run.join(
                        project,
                        (project.c.tenant_id == evaluation_run.c.tenant_id)
                        & (project.c.id == evaluation_run.c.project_id),
                    )
                )
                .where(evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id)
            )
            .mappings()
            .one()
        )
        document = _normalize_specialist_report_document(
            document,
            product_title=str(identity["name"]),
        )
        self._validate(self._specialist_report_schema, document, "SpecialistReportDocumentV2")
        if (
            document["run_id"] != str(run_id)
            or document["project_id"] != str(identity["project_id"])
            or document["product_version_id"] != str(identity["product_version_id"])
            or document["product_title"] != str(identity["name"])
            or document["agent_code"] != agent_code
        ):
            raise SupervisorWorkflowError("specialist report identity does not match the assigned Run")
        if (identity["state_flags"] or {}).get("architecture_generation") == "supervisor-1p4-report-v3":
            document = self._specialist_report_v3.adapt(
                document,
                locale=(identity["state_flags"] or {}).get("locale") or "zh-CN",
            )
            self._validate(self._specialist_report_schema_v3, document, "SpecialistReportDocumentV3")
        body = _canonical(document)
        digest = hashlib.sha256(body).hexdigest()
        report_id = UUID(str(document["report_id"]))
        object_key = f"tenant/{tenant_id}/run/{run_id}/agent-reports/{agent_code}/report-{report_id}-{digest}.json"
        try:
            stored_digest = self._objects.put_private(object_key, body, "application/json")
            metadata = self._objects.head(object_key)
        except Exception as exc:
            raise SupervisorWorkflowError("specialist report persistence failed") from exc
        if stored_digest != digest or metadata is None or metadata.sha256 != digest:
            raise SupervisorWorkflowError("specialist report persistence could not be reconciled")
        existing = session.execute(
            select(evidence.c.id).where(
                evidence.c.tenant_id == tenant_id,
                evidence.c.run_id == run_id,
                evidence.c.task_id == task_id,
                evidence.c.object_key == object_key,
                evidence.c.sha256 == digest,
            )
        ).scalar_one_or_none()
        if existing is None:
            session.execute(
                evidence.insert().values(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    source_type="AGENT_REPORT",
                    object_key=object_key,
                    sha256=digest,
                    mime_type="application/json",
                    evidence_level="E1",
                    trust_level="E1",
                )
            )
        return {"ref": object_key, "sha256": digest}

    @staticmethod
    def _apply_citation_admission(
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        source: dict[str, Any],
        submitted: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        document = dict(submitted)
        references = tuple(str(item) for item in source.get("evidence_refs", []))
        evidence_rows = (
            session.execute(
                select(
                    evidence.c.id,
                    evidence.c.source_type,
                    evidence.c.evidence_level,
                    evidence.c.valid_until,
                ).where(
                    evidence.c.tenant_id == tenant_id,
                    evidence.c.run_id == run_id,
                    evidence.c.object_key.in_(references),
                )
            )
            .mappings()
            .all()
            if references
            else []
        )
        evidence_ids = tuple(row["id"] for row in evidence_rows)
        locator_rows = (
            session.execute(
                select(
                    evidence_source_locator.c.id,
                    evidence_source_locator.c.evidence_id,
                    evidence_source_locator.c.independence_group,
                    evidence_source_locator.c.source_kind,
                ).where(
                    evidence_source_locator.c.tenant_id == tenant_id,
                    evidence_source_locator.c.evidence_id.in_(evidence_ids),
                )
            )
            .mappings()
            .all()
            if evidence_ids
            else []
        )
        internal_types = {"MATERIAL", "MATERIAL_UNIT", "UPLOAD", "INTERNAL_MATERIAL", "AGENT_REPORT"}
        internal_rows = [row for row in evidence_rows if str(row["source_type"]) in internal_types]
        public_claim = str(source.get("dimension")) == "business-investment" or any(
            marker in str(source.get("claim", "")).lower() for marker in ("market", "市场", "tam", "revenue", "收入")
        )
        external_locators = [row for row in locator_rows if row["source_kind"] != "INTERNAL_MATERIAL"]
        independent_groups = {str(row["independence_group"]) for row in locator_rows}
        located_evidence_ids = {item["evidence_id"] for item in locator_rows}
        independent_groups.update(
            f"internal:{row['id']}" for row in internal_rows if row["id"] not in located_evidence_ids
        )
        valid_until = [row["valid_until"] for row in evidence_rows if row["valid_until"] is not None]
        expired = any(item < now for item in valid_until)
        near_expiry = not expired and any(item <= now + timedelta(days=30) for item in valid_until)
        freshness_status = "EXPIRED" if expired else "NEAR_EXPIRY" if near_expiry else "VALID"
        freshness_score = 0.25 if expired else 0.6 if near_expiry else 1.0

        def level(value: object) -> int:
            try:
                return max(0, min(5, int(str(value).removeprefix("E"))))
            except ValueError:
                return 0

        maximum_level = max((level(row["evidence_level"]) for row in evidence_rows), default=0)
        support_strength = (
            "NONE"
            if not evidence_rows
            else "STRONG"
            if maximum_level >= 4 and independent_groups
            else "MODERATE"
            if maximum_level >= 3 and independent_groups
            else "WEAK"
        )
        missing_public_locator = public_claim and not external_locators and not internal_rows
        decision = str(document["decision"])
        if not evidence_rows or missing_public_locator:
            decision = "NEEDS_MORE" if decision == "NEEDS_MORE" else "REJECTED"
        citation_status = (
            "REJECTED"
            if decision == "REJECTED"
            else "PENDING_VALIDATION"
            if decision == "NEEDS_MORE" or expired
            else "DOWNGRADED"
            if decision == "DOWNGRADED"
            else "VERIFIED"
        )
        score_bearing = citation_status in {"VERIFIED", "DOWNGRADED"} and support_strength != "NONE"
        document.update(
            {
                "schema_version": "4.0",
                "decision": decision,
                "freshness_status": freshness_status,
                "freshness_score": freshness_score,
                "support_strength": support_strength,
                "independent_source_count": len(independent_groups),
                "evidence_ids": [str(item) for item in evidence_ids],
                "source_locator_ids": [str(row["id"]) for row in locator_rows],
                "citation_status": citation_status,
                "score_bearing": score_bearing,
            }
        )
        if decision != "NEEDS_MORE":
            document["remediation_target"] = None
        return document

    def _assert_domain_terminal(
        self, session: Session, tenant_id: UUID, run_id: UUID, _audit_round: int, now: datetime
    ) -> bool:
        rows = (
            session.execute(
                select(task.c.status, task.c.required, task.c.last_failure_class, task.c.agent_identity_ref).where(
                    task.c.tenant_id == tenant_id,
                    task.c.run_id == run_id,
                    task.c.stage_code.in_(("DOMAIN_REVIEW", "TARGETED_REMEDIATION")),
                )
            )
            .mappings()
            .all()
        )
        relevant = [row for row in rows if str(row["agent_identity_ref"]).endswith(("@4.0", "@5.0", "@6.0"))]
        unknown = next(
            (
                row
                for row in relevant
                if row["last_failure_class"] in _FAIL_CLOSED_CLASSES or row["status"] == "NEEDS_ATTENTION"
            ),
            None,
        )
        if unknown is not None:
            self._fail_closed(session, tenant_id, run_id, None, str(unknown["last_failure_class"]), now)
            return True
        if not relevant or any(row["status"] not in _TERMINAL for row in relevant):
            raise SupervisorWorkflowError("serial audit waits until all applicable domain tasks are terminal")
        required_failure = next(
            (row for row in relevant if row["required"] and row["status"] in {"KNOWN_FAILED", "FAILED"}), None
        )
        if required_failure is not None:
            session.execute(
                update(evaluation_run)
                .where(evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id)
                .values(
                    status="NEEDS_ATTENTION",
                    current_stage="NEEDS_ATTENTION",
                    attention_reason="REQUIRED_AGENT_FAILED",
                    updated_at=now,
                )
            )
            return True
        return False

    def _materialize_remediation(
        self,
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        documents: list[dict[str, Any]],
        now: datetime,
    ) -> list[UUID]:
        existing = session.execute(
            select(task.c.id).where(
                task.c.tenant_id == tenant_id,
                task.c.run_id == run_id,
                task.c.dispatch_epoch == 1,
                task.c.stage_code == "TARGETED_REMEDIATION",
            )
        ).first()
        if existing is not None:
            raise SupervisorWorkflowError("targeted remediation has already been materialized")
        plan = (
            session.execute(
                select(agent_plan).where(
                    agent_plan.c.tenant_id == tenant_id,
                    agent_plan.c.run_id == run_id,
                    agent_plan.c.status == "ACCEPTED",
                )
            )
            .mappings()
            .one()
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
                code="TARGETED_REMEDIATION",
                ordinal=ordinal,
                status="RUNNING",
                started_at=now,
                completed_at=None,
            )
        )
        task_ids: list[UUID] = []
        for document in documents:
            target = document["remediation_target"]
            agent = str(target["agent_code"])
            task_id = uuid4()
            ticket_id = uuid4()
            deadline = now + timedelta(seconds=3600)
            source_task = (
                session.execute(
                    select(task.c.id, task.c.required, task.c.tool_allowlist, task.c.agent_identity_ref).where(
                        task.c.tenant_id == tenant_id,
                        task.c.run_id == run_id,
                        task.c.agent_identity_ref.in_((f"{agent}@4.0", f"{agent}@5.0", f"{agent}@6.0")),
                        task.c.dispatch_epoch == 0,
                    )
                )
                .mappings()
                .one()
            )
            agent_version = str(source_task["agent_identity_ref"]).split("@", 1)[1]
            skill_code = _REPORT_V22_SKILL_BY_AGENT[agent] if agent_version == "6.0" else _SKILL_BY_AGENT[agent]
            selected_skill_version = (
                _REPORT_V22_SKILL_VERSION_BY_AGENT[agent] if agent_version == "6.0" else _SKILL_VERSION_BY_AGENT[agent]
            )
            skill_version_id = session.execute(
                select(skill_version.c.id).where(
                    skill_version.c.skill_code == skill_code,
                    skill_version.c.version == selected_skill_version,
                )
            ).scalar_one_or_none()
            if skill_version_id is None:
                raise SupervisorWorkflowError(
                    f"registered Skill version is missing: {skill_code}@{selected_skill_version}"
                )
            tool_allowlist = list(source_task["tool_allowlist"] or [])
            public_summary = {
                "schema_version": "3.0",
                "ticket_id": str(ticket_id),
                "run_id": str(run_id),
                "task_id": str(task_id),
                "plan_id": str(plan["id"]),
                "plan_version": int(plan["plan_version"]),
                "dispatch_epoch": 1,
                "target_agent": agent,
                "objective": str(target["question"]),
                "input_refs": [f"finding:{target['finding_id']}"],
                "analysis_dimensions": ["targeted-evidence-remediation"],
                "region_scope": ["as-assigned"],
                "as_of": now.date().isoformat(),
                "tool_policy": tool_allowlist,
                "success_conditions": [str(target["required_evidence"])],
                "required": bool(source_task["required"]),
                "deadline_at": deadline.isoformat().replace("+00:00", "Z"),
                "report_contract": "specialist-report.v2" if agent_version == "6.0" else "domain-report-ref.v1",
                "handoff_contract": "agent-handoff.v3",
            }
            digest = hashlib.sha256(_canonical(public_summary)).hexdigest()
            session.execute(
                task.insert().values(
                    id=task_id,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    stage_id=stage_id,
                    agent_identity_id=None,
                    skill_version_id=skill_version_id,
                    stage_code="TARGETED_REMEDIATION",
                    agent_identity_ref=f"{agent}@{agent_version}",
                    skill_ref=skill_code,
                    skill_version=selected_skill_version,
                    status="PENDING",
                    lease_token=None,
                    idempotency_key=f"v4-remediation:{document['audit_id']}",
                    dependencies=[],
                    tool_allowlist=tool_allowlist,
                    budget_slice={"suggested_usd": 0},
                    timeout_seconds=3600,
                    success_condition=[str(target["required_evidence"])],
                    evidence_requirement=str(target["required_evidence"]),
                    required=bool(source_task["required"]),
                    correction_attempts=0,
                    transient_retries=0,
                    dispatch_epoch=1,
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
                    dispatch_epoch=1,
                    target_agent=agent,
                    ticket_sha256=digest,
                    public_summary=public_summary,
                    usage_baseline=None,
                    status="PREPARED",
                    expires_at=deadline,
                    created_at=now,
                    delivered_at=None,
                )
            )
            for scope in session.execute(
                select(task_material_scope).where(
                    task_material_scope.c.tenant_id == tenant_id,
                    task_material_scope.c.task_id == source_task["id"],
                )
            ).mappings():
                session.execute(
                    task_material_scope.insert().values(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        run_id=run_id,
                        task_id=task_id,
                        plan_id=scope["plan_id"],
                        material_id=scope["material_id"],
                        analysis_id=scope["analysis_id"],
                        unit_ids=scope["unit_ids"],
                        unit_refs=scope["unit_refs"],
                        reason=f"targeted remediation: {scope['reason']}",
                        required=scope["required"],
                        scope_sha256=scope["scope_sha256"],
                        created_at=now,
                    )
                )
            task_ids.append(task_id)
        return task_ids

    @staticmethod
    def _remediation_target(session: Session, tenant_id: UUID, task_id: UUID) -> UUID | None:
        row = session.execute(
            select(agent_task_ticket.c.public_summary).where(
                agent_task_ticket.c.tenant_id == tenant_id,
                agent_task_ticket.c.task_id == task_id,
                agent_task_ticket.c.dispatch_epoch == 1,
            )
        ).scalar_one_or_none()
        if not row:
            return None
        references = row.get("input_refs", [])
        target = next((item for item in references if str(item).startswith("finding:")), None)
        return None if target is None else UUID(str(target).removeprefix("finding:"))

    def _verify_content_ref(self, reference: dict[str, Any]) -> None:
        metadata = self._objects.head(str(reference["ref"]))
        if metadata is None or metadata.sha256 != reference["sha256"]:
            raise SupervisorWorkflowError("content ref is missing or does not match immutable object metadata")

    @staticmethod
    def _validate(schema: dict[str, Any], document: dict[str, Any], label: str) -> None:
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
            key=lambda item: item.json_path,
        )
        if errors:
            raise SupervisorWorkflowError(f"{label} contract violation at {errors[0].json_path}")

    @staticmethod
    def _fail_closed(
        session: Session,
        tenant_id: UUID,
        run_id: UUID,
        task_id: UUID | None,
        failure_class: str,
        now: datetime,
    ) -> None:
        if task_id is not None:
            session.execute(
                update(task)
                .where(task.c.tenant_id == tenant_id, task.c.id == task_id)
                .values(
                    status="NEEDS_ATTENTION",
                    last_failure_class=failure_class,
                    last_error="automatic retry prohibited",
                    updated_at=now,
                )
            )
        session.execute(
            update(evaluation_run)
            .where(evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id)
            .values(
                status="NEEDS_ATTENTION",
                current_stage="NEEDS_ATTENTION",
                last_failure_class=failure_class,
                attention_reason="automatic retry prohibited for unknown external state or cost",
                updated_at=now,
            )
        )

    @staticmethod
    def _require_enabled() -> None:
        if not supervisor_1p4_enabled():
            raise IntakeValidationError("supervisor 1+4 generation is disabled")


__all__ = [
    "AuditRoundResult",
    "DomainIngestionResult",
    "SupervisorAuditApplication",
    "SupervisorWorkflowError",
]
