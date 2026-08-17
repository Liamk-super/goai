"""Maintenance-only retention deletion with body-free tombstone evidence."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    deletion_tombstone,
    evaluation_run,
    evidence,
    finding,
    material,
    memory_candidate,
    memory_item,
    product_version,
    project,
    rag_retrieval,
    report,
    retention_policy,
    run_conversation_message,
    trace_metadata,
)


class ObjectDeletionPort(Protocol):
    def delete(self, object_key: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    temporary_days: int = 7
    evidence_days: int = 90
    trace_body_days: int = 30
    metrics_days: int = 365
    audit_days: int = 365


@dataclass(frozen=True, slots=True)
class DeletionReport:
    tenant_id: UUID
    target_type: str
    target_id: UUID
    database_bodies: int
    objects: int
    vectors: int
    cache: str
    derived_indexes: int
    tombstone_id: UUID
    completed_at: str


class RetentionApplication:
    """Runs only with a database-owner maintenance session, never request RLS sessions."""

    def __init__(self, maintenance_sessions: sessionmaker[Session], objects: ObjectDeletionPort) -> None:
        self._sessions = maintenance_sessions
        self._objects = objects

    def policy(self, tenant_id: UUID) -> RetentionPolicy:
        with self._sessions() as session:
            row = (
                session.execute(select(retention_policy).where(retention_policy.c.tenant_id == tenant_id))
                .mappings()
                .first()
            )
            if row is None:
                return RetentionPolicy()
            return RetentionPolicy(
                row["temporary_days"],
                row["evidence_days"],
                row["trace_body_days"],
                row["metrics_days"],
                row["audit_days"],
            )

    def configure_policy(self, tenant_id: UUID, policy: RetentionPolicy) -> RetentionPolicy:
        self._validate_policy(policy)
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            self._require_owner(session)
            existing = session.execute(
                select(retention_policy.c.id).where(retention_policy.c.tenant_id == tenant_id)
            ).scalar_one_or_none()
            values = {**asdict(policy), "updated_at": now}
            if existing is None:
                session.execute(
                    retention_policy.insert().values(id=uuid4(), tenant_id=tenant_id, created_at=now, **values)
                )
            else:
                session.execute(
                    update(retention_policy).where(retention_policy.c.tenant_id == tenant_id).values(**values)
                )
        return policy

    def delete_run(self, tenant_id: UUID, run_id: UUID, *, actor_id: str, reason: str) -> DeletionReport:
        return self._delete(tenant_id, "RUN", run_id, actor_id=actor_id, reason=reason)

    def delete_project(self, tenant_id: UUID, project_id: UUID, *, actor_id: str, reason: str) -> DeletionReport:
        return self._delete(tenant_id, "PROJECT", project_id, actor_id=actor_id, reason=reason)

    def _delete(
        self, tenant_id: UUID, target_type: str, target_id: UUID, *, actor_id: str, reason: str
    ) -> DeletionReport:
        if not actor_id.strip() or not reason.strip():
            raise ValueError("deletion actor and reason are required")
        with self._sessions() as session:
            self._require_owner(session)
            project_ids, version_ids, run_ids = self._scope_ids(session, tenant_id, target_type, target_id)
            keys = self._object_keys(session, tenant_id, version_ids, run_ids)
        object_count = 0
        for key in keys:
            if self._objects.delete(key):
                object_count += 1

        completed = datetime.now(UTC)
        tombstone_id = uuid4()
        with self._sessions.begin() as session:
            self._require_owner(session)
            session.execute(text("SET LOCAL app.retention_delete = 'on'"))
            body_count = 0
            body_count += self._redact_materials(session, tenant_id, version_ids)
            body_count += self._redact_evidence(session, tenant_id, run_ids)
            body_count += self._redact_findings(session, tenant_id, run_ids)
            body_count += self._redact_reports(session, tenant_id, run_ids)
            body_count += self._redact_memory(session, tenant_id, project_ids)
            vectors = self._delete_where(session, rag_retrieval, tenant_id, rag_retrieval.c.project_id.in_(project_ids))
            traces = self._delete_where(session, trace_metadata, tenant_id, trace_metadata.c.run_id.in_(run_ids))
            result = {
                "database_bodies": body_count,
                "objects": object_count,
                "vectors": vectors,
                "cache": "not_configured",
                "derived_indexes": traces,
            }
            digest = hashlib.sha256(f"{tenant_id}:{target_type}:{target_id}".encode()).hexdigest()
            session.execute(
                deletion_tombstone.insert().values(
                    id=tombstone_id,
                    tenant_id=tenant_id,
                    target_type=target_type,
                    target_id=target_id,
                    target_sha256=digest,
                    actor_id=actor_id,
                    reason=reason[:500],
                    result=result,
                    occurred_at=completed,
                )
            )
        return DeletionReport(
            tenant_id,
            target_type,
            target_id,
            body_count,
            object_count,
            vectors,
            "not_configured",
            traces,
            tombstone_id,
            completed.isoformat().replace("+00:00", "Z"),
        )

    @staticmethod
    def _scope_ids(
        session: Session, tenant_id: UUID, target_type: str, target_id: UUID
    ) -> tuple[list[UUID], list[UUID], list[UUID]]:
        if target_type == "RUN":
            run = session.execute(
                select(evaluation_run.c.project_id, evaluation_run.c.product_version_id).where(
                    evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == target_id
                )
            ).one()
            project_ids, version_ids, run_ids = [run.project_id], [run.product_version_id], [target_id]
        elif target_type == "PROJECT":
            session.execute(
                select(project.c.id).where(project.c.tenant_id == tenant_id, project.c.id == target_id)
            ).scalar_one()
            project_ids = [target_id]
            run_ids = list(
                session.execute(
                    select(evaluation_run.c.id).where(
                        evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.project_id == target_id
                    )
                ).scalars()
            )
            version_ids = list(
                session.execute(
                    select(product_version.c.id).where(
                        product_version.c.tenant_id == tenant_id, product_version.c.project_id.in_(project_ids)
                    )
                ).scalars()
            )
        else:
            raise ValueError("target type must be RUN or PROJECT")
        return project_ids, version_ids, run_ids

    @staticmethod
    def _object_keys(session: Session, tenant_id: UUID, version_ids: list[UUID], run_ids: list[UUID]) -> list[str]:
        keys: list[str] = []
        if version_ids:
            keys.extend(
                session.execute(
                    select(material.c.object_key).where(
                        material.c.tenant_id == tenant_id, material.c.product_version_id.in_(version_ids)
                    )
                ).scalars()
            )
        if run_ids:
            keys.extend(
                session.execute(
                    select(evidence.c.object_key).where(
                        evidence.c.tenant_id == tenant_id, evidence.c.run_id.in_(run_ids)
                    )
                ).scalars()
            )
            keys.extend(
                session.execute(
                    select(report.c.object_key).where(report.c.tenant_id == tenant_id, report.c.run_id.in_(run_ids))
                ).scalars()
            )
            keys.extend(
                session.execute(
                    select(run_conversation_message.c.object_key).where(
                        run_conversation_message.c.tenant_id == tenant_id,
                        run_conversation_message.c.run_id.in_(run_ids),
                    )
                ).scalars()
            )
        return sorted(set(keys))

    @staticmethod
    def _redact_materials(session: Session, tenant_id: UUID, version_ids: list[UUID]) -> int:
        if not version_ids:
            return 0
        rows = session.execute(
            select(material.c.id, material.c.object_key).where(
                material.c.tenant_id == tenant_id, material.c.product_version_id.in_(version_ids)
            )
        ).all()
        for row in rows:
            digest = hashlib.sha256(row.object_key.encode()).hexdigest()
            session.execute(
                update(material)
                .where(material.c.id == row.id, material.c.tenant_id == tenant_id)
                .values(object_key=f"deleted/{digest}", display_name="[deleted]", object_metadata={})
            )
        return len(rows)

    @staticmethod
    def _redact_evidence(session: Session, tenant_id: UUID, run_ids: list[UUID]) -> int:
        if not run_ids:
            return 0
        rows = session.execute(
            select(evidence.c.id, evidence.c.object_key).where(
                evidence.c.tenant_id == tenant_id, evidence.c.run_id.in_(run_ids)
            )
        ).all()
        for row in rows:
            digest = hashlib.sha256(row.object_key.encode()).hexdigest()
            session.execute(
                update(evidence)
                .where(evidence.c.id == row.id, evidence.c.tenant_id == tenant_id)
                .values(object_key=f"deleted/{digest}", summary="[deleted by retention policy]")
            )
        return len(rows)

    @staticmethod
    def _redact_findings(session: Session, tenant_id: UUID, run_ids: list[UUID]) -> int:
        if not run_ids:
            return 0
        result: Any = session.execute(
            update(finding)
            .where(finding.c.tenant_id == tenant_id, finding.c.run_id.in_(run_ids))
            .values(statement="[deleted by retention policy]", structured_result={})
        )
        return int(result.rowcount or 0)

    @staticmethod
    def _redact_reports(session: Session, tenant_id: UUID, run_ids: list[UUID]) -> int:
        if not run_ids:
            return 0
        rows = session.execute(
            select(report.c.id, report.c.object_key).where(
                report.c.tenant_id == tenant_id, report.c.run_id.in_(run_ids)
            )
        ).all()
        for row in rows:
            digest = hashlib.sha256(row.object_key.encode()).hexdigest()
            session.execute(
                update(report)
                .where(report.c.id == row.id, report.c.tenant_id == tenant_id)
                .values(object_key=f"deleted/{digest}", action_items=[])
            )
        return len(rows)

    @staticmethod
    def _redact_memory(session: Session, tenant_id: UUID, project_ids: list[UUID]) -> int:
        count = 0
        for table, column, values in (
            (memory_item, memory_item.c.content, {"content": {}, "search_text": None, "validity_status": "DELETED"}),
            (memory_candidate, memory_candidate.c.candidate, {"candidate": {}, "status": "DELETED"}),
        ):
            del column
            result: Any = session.execute(
                update(table)
                .where(table.c.tenant_id == tenant_id, table.c.project_id.in_(project_ids))
                .values(**values)
            )
            count += int(result.rowcount or 0)
        return count

    @staticmethod
    def _delete_where(session: Session, table: Any, tenant_id: UUID, condition: Any) -> int:
        result: Any = session.execute(delete(table).where(table.c.tenant_id == tenant_id, condition))
        return int(result.rowcount or 0)

    @staticmethod
    def _require_owner(session: Session) -> None:
        row = session.execute(text("SELECT current_user, session_user")).one()
        if row.current_user != row.session_user:
            raise PermissionError("retention deletion requires the dedicated maintenance connection")

    @staticmethod
    def _validate_policy(policy: RetentionPolicy) -> None:
        values = asdict(policy)
        if any(not isinstance(value, int) or value <= 0 for value in values.values()):
            raise ValueError("retention durations must be positive whole days")


__all__ = ["DeletionReport", "RetentionApplication", "RetentionPolicy"]
