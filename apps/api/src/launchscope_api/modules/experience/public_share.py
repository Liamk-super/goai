from __future__ import annotations

import hashlib
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    agent_report_artifact,
    evaluation_run,
    evidence,
    public_demo_disclosure_acceptance,
    public_demo_share,
    report,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_domain.value_objects import TenantScope


class PublicShareNotFound(LookupError):
    pass


class PublicSharePublishError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PublicDemoShareGrant:
    tenant_id: UUID
    share_id: UUID
    run_id: UUID
    report_id: UUID
    include_agent_reports: bool
    include_evidence: bool


class PublicDemoShareApplication:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def publish(self, actor: Actor, report_id: UUID, *, idempotency_key: str) -> dict[str, object]:
        if not idempotency_key.strip():
            raise PublicSharePublishError("Idempotency-Key is required")
        now = datetime.now(UTC)
        with tenant_transaction(
            self._sessions,
            TenantScope(actor.tenant_id),
            actor_id=actor.actor_id,
        ) as session:
            target = session.execute(
                select(
                    report.c.id.label("report_id"),
                    report.c.run_id,
                    evaluation_run.c.project_id,
                    evaluation_run.c.product_version_id,
                    evaluation_run.c.status.label("run_status"),
                )
                .select_from(
                    report.join(
                        evaluation_run,
                        and_(
                            evaluation_run.c.tenant_id == report.c.tenant_id,
                            evaluation_run.c.id == report.c.run_id,
                        ),
                    )
                )
                .where(
                    report.c.tenant_id == actor.tenant_id,
                    report.c.id == report_id,
                    report.c.status == "COMMITTED",
                )
            ).mappings().first()
            if target is None:
                raise PublicSharePublishError("committed report was not found")
            if target["run_status"] != "COMPLETED":
                raise PublicSharePublishError("public Demo share requires a completed Run")
            disclosed = session.execute(
                select(
                    public_demo_disclosure_acceptance.c.id,
                    public_demo_disclosure_acceptance.c.run_id,
                ).where(
                    public_demo_disclosure_acceptance.c.tenant_id == actor.tenant_id,
                    public_demo_disclosure_acceptance.c.project_id == target["project_id"],
                    public_demo_disclosure_acceptance.c.product_version_id == target["product_version_id"],
                    public_demo_disclosure_acceptance.c.policy_version == "public-demo-evidence-v1",
                )
            ).mappings().first()
            if disclosed is None:
                raise PublicSharePublishError("public Demo disclosure is required before publishing")
            if disclosed["run_id"] is None:
                session.execute(
                    update(public_demo_disclosure_acceptance)
                    .where(
                        public_demo_disclosure_acceptance.c.tenant_id == actor.tenant_id,
                        public_demo_disclosure_acceptance.c.id == disclosed["id"],
                        public_demo_disclosure_acceptance.c.run_id.is_(None),
                    )
                    .values(run_id=target["run_id"])
                )
            existing = session.execute(
                select(public_demo_share).where(
                    public_demo_share.c.tenant_id == actor.tenant_id,
                    public_demo_share.c.run_id == target["run_id"],
                    public_demo_share.c.report_id == report_id,
                    public_demo_share.c.status == "ACTIVE",
                    public_demo_share.c.include_agent_reports.is_(True),
                    public_demo_share.c.include_evidence.is_(True),
                )
            ).mappings().first()
            if existing is None:
                share_id = uuid4()
                created_at = now
                token = str(share_id)
                session.execute(
                    public_demo_share.insert().values(
                        id=share_id,
                        tenant_id=actor.tenant_id,
                        run_id=target["run_id"],
                        report_id=report_id,
                        token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                        status="ACTIVE",
                        include_agent_reports=True,
                        include_evidence=True,
                        created_at=created_at,
                        revoked_at=None,
                    )
                )
            else:
                share_id = UUID(str(existing["id"]))
                created_at = existing["created_at"]
                token = str(share_id)
        return {
            "share_id": str(share_id),
            "run_id": str(target["run_id"]),
            "report_id": str(report_id),
            "token": token,
            "status": "ACTIVE",
            "include_agent_reports": True,
            "include_evidence": True,
            "created_at": (created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)).isoformat(),
        }


class PublicDemoShareResolver:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def resolve(self, token: str) -> PublicDemoShareGrant:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._sessions() as session, session.begin():
            row = session.execute(
                text("SELECT * FROM launchscope_resolve_public_demo_share(:token_sha256)"),
                {"token_sha256": digest},
            ).mappings().first()
        if row is None:
            raise PublicShareNotFound("shared Demo resource was not found")
        return PublicDemoShareGrant(
            tenant_id=UUID(str(row["tenant_id"])),
            share_id=UUID(str(row["share_id"])),
            run_id=UUID(str(row["run_id"])),
            report_id=UUID(str(row["report_id"])),
            include_agent_reports=bool(row["include_agent_reports"]),
            include_evidence=bool(row["include_evidence"]),
        )

    def supervisor_metadata(self, grant: PublicDemoShareGrant, report_id: UUID) -> dict[str, object]:
        if report_id != grant.report_id:
            raise PublicShareNotFound("shared Demo resource was not found")
        with self._scope(grant) as session:
            row = session.execute(
                select(report).where(
                    report.c.tenant_id == grant.tenant_id,
                    report.c.id == report_id,
                    report.c.run_id == grant.run_id,
                    report.c.status == "COMMITTED",
                )
            ).mappings().first()
        if row is None:
            raise PublicShareNotFound("shared Demo resource was not found")
        return {
            "report_id": str(row["id"]),
            "run_id": str(grant.run_id),
            "object_key": str(row["object_key"]),
            "sha256": str(row["sha256"]),
            "mime_type": "application/json",
            "created_at": row["created_at"].isoformat(),
        }

    def agent_metadata(self, grant: PublicDemoShareGrant, agent_code: str) -> dict[str, object]:
        if not grant.include_agent_reports:
            raise PublicShareNotFound("shared Demo resource was not found")
        with self._scope(grant) as session:
            row = session.execute(
                select(agent_report_artifact)
                .where(
                    agent_report_artifact.c.tenant_id == grant.tenant_id,
                    agent_report_artifact.c.run_id == grant.run_id,
                    agent_report_artifact.c.agent_code == agent_code,
                    agent_report_artifact.c.status == "AVAILABLE",
                )
                .order_by(
                    agent_report_artifact.c.revision.desc(),
                    agent_report_artifact.c.created_at.desc(),
                )
                .limit(1)
            ).mappings().first()
        if row is None:
            raise PublicShareNotFound("shared Demo resource was not found")
        return {
            "report_id": str(row["id"]),
            "run_id": str(grant.run_id),
            "agent_code": agent_code,
            "object_key": str(row["object_key"]),
            "sha256": str(row["sha256"]),
            "mime_type": str(row["mime_type"]),
            "created_at": row["created_at"].isoformat(),
            "revision": int(row["revision"]),
            "supervisor_report_id": str(grant.report_id),
        }

    def evidence_metadata(self, grant: PublicDemoShareGrant, evidence_id: UUID) -> dict[str, object]:
        if not grant.include_evidence:
            raise PublicShareNotFound("shared Demo resource was not found")
        with self._scope(grant) as session:
            row = session.execute(
                select(evidence).where(
                    evidence.c.tenant_id == grant.tenant_id,
                    evidence.c.run_id == grant.run_id,
                    evidence.c.id == evidence_id,
                )
            ).mappings().first()
        if row is None:
            raise PublicShareNotFound("shared Demo resource was not found")
        return {
            "evidence_id": str(row["id"]),
            "run_id": str(grant.run_id),
            "object_key": str(row["object_key"]),
            "sha256": str(row["sha256"]),
            "mime_type": str(row["mime_type"]),
        }

    def _scope(self, grant: PublicDemoShareGrant) -> AbstractContextManager[Session]:
        return tenant_transaction(
            self._sessions,
            TenantScope(grant.tenant_id),
            actor_id=f"public-demo-share:{grant.share_id}",
        )


__all__ = [
    "PublicDemoShareApplication",
    "PublicDemoShareGrant",
    "PublicDemoShareResolver",
    "PublicShareNotFound",
    "PublicSharePublishError",
]
