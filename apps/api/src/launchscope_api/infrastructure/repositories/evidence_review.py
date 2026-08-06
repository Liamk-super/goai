"""PostgreSQL adapter for EvidenceReview and immutable finding lineage."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from launchscope_domain.aggregates.evidence_review import (
    ConflictRecord,
    Evidence,
    EvidenceAudit,
    EvidenceReview,
    Finding,
)
from launchscope_domain.enums import (
    DimensionCode,
    EvidenceAuditDecision,
    EvidenceLevel,
    EvidenceSourceType,
    FindingGrade,
)
from launchscope_domain.ports.repositories import EvidenceReviewRepository as EvidenceReviewPort
from launchscope_domain.value_objects import EvidenceRef, TenantScope, TimeScope

from ..db.schema import conflict_record, evidence, evidence_audit, finding, finding_evidence
from .base import (
    assert_aggregate_scope,
    insert_if_absent,
    json_value,
    require_scope_id,
    require_utc_datetime,
    utc_datetime,
)


class SqlAlchemyEvidenceReviewRepository(EvidenceReviewPort):
    """Persist evidence and conclusions without allowing historical overwrite."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, resource_id: UUID, scope: TenantScope) -> EvidenceReview | None:
        run_id = require_scope_id(scope, "run_id")
        evidence_rows = (
            self.session.execute(
                select(evidence).where(evidence.c.tenant_id == scope.tenant_id, evidence.c.run_id == run_id)
            )
            .mappings()
            .all()
        )
        finding_rows = (
            self.session.execute(
                select(finding).where(finding.c.tenant_id == scope.tenant_id, finding.c.run_id == run_id)
            )
            .mappings()
            .all()
        )
        if not evidence_rows and not finding_rows:
            return None
        evidence_by_id: dict[UUID, Evidence] = {}
        for row in evidence_rows:
            evidence_by_id[row["id"]] = Evidence(
                evidence_id=row["id"],
                scope=scope,
                ref=EvidenceRef(
                    evidence_id=row["id"],
                    object_key=row["object_key"],
                    sha256=row["sha256"],
                    mime_type=row["mime_type"],
                    source_type=EvidenceSourceType(row["source_type"]),
                    trust_level=EvidenceLevel(row["trust_level"]),
                    size_bytes=row["size_bytes"],
                ),
                time_scope=TimeScope(
                    published_at=utc_datetime(row["published_at"]),
                    fetched_at=utc_datetime(row["fetched_at"]),
                    valid_from=utc_datetime(row["valid_from"]),
                    valid_until=utc_datetime(row["valid_until"]),
                    region=row["region"],
                ),
                captured_by_task_id=row["task_id"],
                summary=row["summary"],
                simulated=row["simulated"],
                supersedes_id=row["supersedes_id"],
            )

        links = (
            self.session.execute(select(finding_evidence).where(finding_evidence.c.tenant_id == scope.tenant_id))
            .mappings()
            .all()
        )
        evidence_ids_by_finding: dict[UUID, list[UUID]] = {}
        for link in links:
            evidence_ids_by_finding.setdefault(link["finding_id"], []).append(link["evidence_id"])
        findings = {
            row["id"]: Finding(
                finding_id=row["id"],
                scope=scope,
                dimension_code=DimensionCode(row["dimension_code"]),
                grade=FindingGrade(row["grade"]),
                statement=row["statement"],
                evidence_ids=tuple(evidence_ids_by_finding.get(row["id"], [])),
                is_hypothesis=row["is_hypothesis"],
                submitted_by=row["submitted_by"],
                submitted_at=require_utc_datetime(row["submitted_at"]),
                supersedes_id=row["supersedes_id"],
                simulated=row["simulated"],
                hard_block=row["hard_block"],
                block_reason=row["block_reason"],
            )
            for row in finding_rows
        }
        conflicts = {}
        conflict_rows = (
            self.session.execute(
                select(conflict_record).where(
                    conflict_record.c.tenant_id == scope.tenant_id,
                    conflict_record.c.run_id == run_id,
                )
            )
            .mappings()
            .all()
        )
        for row in conflict_rows:
            refs = row["conflicting_refs"] if isinstance(row["conflicting_refs"], list) else []
            refs = [row["finding_id"], *refs]
            if len(set(refs)) < 2:
                continue
            conflicts[row["id"]] = ConflictRecord(
                conflict_id=row["id"],
                scope=scope,
                finding_ids=tuple(refs),
                reason=row["reason"],
                resolved=row["resolution_status"] == "RESOLVED",
            )
        audit_rows = (
            self.session.execute(
                select(evidence_audit).where(
                    evidence_audit.c.tenant_id == scope.tenant_id,
                    evidence_audit.c.run_id == run_id,
                )
            )
            .mappings()
            .all()
        )
        audits = [
            EvidenceAudit(
                audit_id=row["id"],
                finding_id=row["finding_id"],
                decision=EvidenceAuditDecision(row["decision"]),
                auditor_id=row["auditor_id"],
                reason=row["reason"],
                audited_at=require_utc_datetime(row["audited_at"]),
            )
            for row in audit_rows
        ]
        return EvidenceReview(
            scope=scope,
            evidence=evidence_by_id,
            findings=findings,
            conflicts=conflicts,
            audits=audits,
        )

    def save(self, aggregate: EvidenceReview) -> None:
        scope = aggregate.scope
        run_id = require_scope_id(scope, "run_id")
        assert_aggregate_scope(aggregate, scope)
        for evidence_item in aggregate.evidence_items:
            if evidence_item.scope.tenant_id != scope.tenant_id:
                raise ValueError("evidence belongs to another tenant")
            insert_if_absent(
                self.session,
                evidence,
                {
                    "id": evidence_item.evidence_id,
                    "tenant_id": scope.tenant_id,
                    "run_id": run_id,
                    "task_id": evidence_item.captured_by_task_id,
                    "material_id": None,
                    "source_type": evidence_item.ref.source_type,
                    "object_key": evidence_item.ref.object_key,
                    "sha256": evidence_item.ref.sha256,
                    "size_bytes": evidence_item.ref.size_bytes,
                    "mime_type": evidence_item.ref.mime_type,
                    "evidence_level": evidence_item.ref.trust_level,
                    "trust_level": evidence_item.ref.trust_level,
                    "summary": evidence_item.summary,
                    "published_at": evidence_item.time_scope.published_at,
                    "fetched_at": evidence_item.time_scope.fetched_at,
                    "valid_from": evidence_item.time_scope.valid_from,
                    "valid_until": evidence_item.time_scope.valid_until,
                    "region": evidence_item.time_scope.region,
                    "simulated": evidence_item.simulated,
                    "supersedes_id": evidence_item.supersedes_id,
                    "created_at": evidence_item.time_scope.fetched_at or datetime.now(UTC),
                },
                resource_id=evidence_item.evidence_id,
            )
        for finding_item in aggregate.finding_items:
            if finding_item.scope.tenant_id != scope.tenant_id:
                raise ValueError("finding belongs to another tenant")
            insert_if_absent(
                self.session,
                finding,
                {
                    "id": finding_item.finding_id,
                    "tenant_id": scope.tenant_id,
                    "run_id": run_id,
                    "task_id": None,
                    "dimension_code": finding_item.dimension_code.value,
                    "grade": finding_item.grade.value,
                    "claim_type": "HYPOTHESIS" if finding_item.is_hypothesis else "FINDING",
                    "statement": finding_item.statement,
                    "is_hypothesis": finding_item.is_hypothesis,
                    "submitted_by": finding_item.submitted_by,
                    "submitted_at": finding_item.submitted_at,
                    "supersedes_id": finding_item.supersedes_id,
                    "structured_result": json_value({"statement": finding_item.statement}),
                    "simulated": finding_item.simulated,
                    "hard_block": finding_item.hard_block,
                    "block_reason": finding_item.block_reason,
                },
                resource_id=finding_item.finding_id,
            )
            for evidence_id in finding_item.evidence_ids:
                link_exists = self.session.execute(
                    select(finding_evidence.c.finding_id).where(
                        finding_evidence.c.tenant_id == scope.tenant_id,
                        finding_evidence.c.finding_id == finding_item.finding_id,
                        finding_evidence.c.evidence_id == evidence_id,
                    )
                ).first()
                if link_exists is None:
                    self.session.execute(
                        finding_evidence.insert().values(
                            tenant_id=scope.tenant_id,
                            finding_id=finding_item.finding_id,
                            evidence_id=evidence_id,
                            relation_type="SUPPORTS",
                        )
                    )
        for conflict_item in aggregate.conflicts.values():
            insert_if_absent(
                self.session,
                conflict_record,
                {
                    "id": conflict_item.conflict_id,
                    "tenant_id": scope.tenant_id,
                    "run_id": run_id,
                    "finding_id": conflict_item.finding_ids[0],
                    "conflicting_refs": json_value(conflict_item.finding_ids[1:]),
                    "resolution_status": "RESOLVED" if conflict_item.resolved else "OPEN",
                    "reason": conflict_item.reason,
                    "created_at": datetime_for_conflict(conflict_item),
                },
                resource_id=conflict_item.conflict_id,
            )
        for audit_item in aggregate.audit_items:
            insert_if_absent(
                self.session,
                evidence_audit,
                {
                    "id": audit_item.audit_id,
                    "tenant_id": scope.tenant_id,
                    "run_id": run_id,
                    "finding_id": audit_item.finding_id,
                    "decision": audit_item.decision.value,
                    "auditor_id": audit_item.auditor_id,
                    "reason": audit_item.reason,
                    "audited_at": audit_item.audited_at,
                },
                resource_id=audit_item.audit_id,
            )


def datetime_for_conflict(item: ConflictRecord) -> datetime:
    # ConflictRecord intentionally has no timestamp in the domain contract;
    # database creation time is the only persistence metadata needed here.
    return datetime.now(UTC)


EvidenceReviewRepositoryAdapter = SqlAlchemyEvidenceReviewRepository

__all__ = ["EvidenceReviewRepositoryAdapter", "SqlAlchemyEvidenceReviewRepository"]
