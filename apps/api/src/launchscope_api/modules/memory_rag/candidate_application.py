"""Promotion of evidence-bound, user-confirmed project memory only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from launchscope_api.infrastructure.db.schema import (
    evidence_audit,
    finding,
    finding_evidence,
    memory_candidate,
    memory_item,
    product_version,
)
from launchscope_domain.enums import EvidenceAuditDecision
from launchscope_domain.value_objects import TenantScope


class MemoryPromotionError(ValueError):
    """A memory candidate did not meet the controlled-write policy."""


@dataclass(frozen=True, slots=True)
class MemoryCandidateInput:
    item_type: str
    content: dict[str, Any]
    source_finding_id: UUID | None = None
    user_confirmed: bool = False
    simulated: bool = False
    valid_until: datetime | None = None
    region: str | None = None
    permission_scope: str = "PROJECT_MEMBER"


@dataclass(frozen=True, slots=True)
class PromotedMemory:
    candidate_id: UUID
    memory_id: UUID
    status: str


class MemoryCandidateApplication:
    """The only T9 write path for durable MemoryItem records.

    PostgreSQL remains authoritative; candidates and promoted items are each
    inserted once.  There is deliberately no update or delete operation here.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def submit(self, scope: TenantScope, candidate: MemoryCandidateInput) -> UUID:
        self._require_project_scope(scope)
        self._require_version_project_membership(scope)
        self._validate_candidate(candidate)
        candidate_id = uuid4()
        self.session.execute(
            insert(memory_candidate).values(
                id=candidate_id,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                source_finding_id=candidate.source_finding_id,
                status="PENDING",
                candidate=self._serialize_candidate(candidate),
                created_at=datetime.now(UTC),
            )
        )
        return candidate_id

    def promote(self, scope: TenantScope, candidate_id: UUID) -> PromotedMemory:
        self._require_project_scope(scope)
        self._require_version_project_membership(scope)
        row = (
            self.session.execute(
                select(memory_candidate).where(
                    memory_candidate.c.id == candidate_id,
                    memory_candidate.c.tenant_id == scope.tenant_id,
                    memory_candidate.c.project_id == scope.project_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise MemoryPromotionError("memory candidate was not found in the project scope")
        if row["status"] != "PENDING":
            raise MemoryPromotionError("memory candidate was already decided")
        raw = dict(row["candidate"])
        source_finding_id = row["source_finding_id"]
        user_confirmed = bool(raw.get("user_confirmed"))
        if not user_confirmed:
            self._require_calibrated_finding(scope, source_finding_id)

        valid_until = raw.get("valid_until")
        if isinstance(valid_until, str):
            valid_until = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
        memory_id = uuid4()
        content = dict(raw["content"])
        content["simulated"] = bool(raw.get("simulated", False))
        content["source_finding_id"] = str(source_finding_id) if source_finding_id else None
        self.session.execute(
            insert(memory_item).values(
                id=memory_id,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                product_version_id=scope.product_version_id,
                source_finding_id=source_finding_id,
                item_type=raw["item_type"],
                validity_status="ACTIVE",
                valid_until=valid_until,
                region=raw.get("region"),
                permission_scope=raw.get("permission_scope", "PROJECT_MEMBER"),
                search_text=self._search_text(content),
                content=content,
                created_at=datetime.now(UTC),
            )
        )
        self.session.execute(
            memory_candidate.update()
            .where(memory_candidate.c.id == candidate_id, memory_candidate.c.status == "PENDING")
            .values(status="PROMOTED")
        )
        return PromotedMemory(candidate_id=candidate_id, memory_id=memory_id, status="PROMOTED")

    @staticmethod
    def _require_project_scope(scope: TenantScope) -> None:
        if scope.project_id is None or scope.product_version_id is None:
            raise MemoryPromotionError("memory operations require project and product version scope")

    def _require_version_project_membership(self, scope: TenantScope) -> None:
        version = self.session.execute(
            select(product_version.c.id).where(
                product_version.c.tenant_id == scope.tenant_id,
                product_version.c.id == scope.product_version_id,
                product_version.c.project_id == scope.project_id,
            )
        ).scalar_one_or_none()
        if version is None:
            raise MemoryPromotionError("product version is not inside the memory project scope")

    @staticmethod
    def _validate_candidate(candidate: MemoryCandidateInput) -> None:
        if not candidate.item_type.strip() or not candidate.content:
            raise MemoryPromotionError("memory candidate requires a type and non-empty content")
        if candidate.simulated and not candidate.content.get("simulated"):
            raise MemoryPromotionError("simulated memory must retain its simulated label")
        time_sensitive = {"POLICY", "PRICE", "TREND"}
        if candidate.item_type.upper() in time_sensitive and candidate.valid_until is None:
            raise MemoryPromotionError("policy, price and trend memory requires valid_until")
        if not candidate.user_confirmed and candidate.source_finding_id is None:
            raise MemoryPromotionError("memory requires user confirmation or an evidence-bound finding")

    def _require_calibrated_finding(self, scope: TenantScope, finding_id: UUID | None) -> None:
        if finding_id is None:
            raise MemoryPromotionError("non-user-confirmed memory requires a source finding")
        found = self.session.execute(
            select(finding.c.id).where(
                finding.c.id == finding_id,
                finding.c.tenant_id == scope.tenant_id,
                finding.c.run_id == scope.run_id,
                finding.c.is_hypothesis.is_(False),
            )
        ).scalar_one_or_none()
        linked_evidence = self.session.execute(
            select(finding_evidence.c.evidence_id).where(
                finding_evidence.c.tenant_id == scope.tenant_id,
                finding_evidence.c.finding_id == finding_id,
            )
        ).first()
        latest = self.session.execute(
            select(evidence_audit.c.decision)
            .where(
                evidence_audit.c.tenant_id == scope.tenant_id,
                evidence_audit.c.finding_id == finding_id,
            )
            .order_by(evidence_audit.c.audited_at.desc())
        ).scalar_one_or_none()
        if found is None or linked_evidence is None or latest != EvidenceAuditDecision.ACCEPTED.value:
            raise MemoryPromotionError("memory source finding is not evidence-bound and calibrated")

    @staticmethod
    def _search_text(content: dict[str, Any]) -> str:
        return " ".join(str(value) for value in content.values() if isinstance(value, (str, int, float, bool)))[:8000]

    @staticmethod
    def _serialize_candidate(candidate: MemoryCandidateInput) -> dict[str, Any]:
        return {
            "item_type": candidate.item_type,
            "content": candidate.content,
            "source_finding_id": str(candidate.source_finding_id) if candidate.source_finding_id else None,
            "user_confirmed": candidate.user_confirmed,
            "simulated": candidate.simulated,
            "valid_until": candidate.valid_until.isoformat() if candidate.valid_until else None,
            "region": candidate.region,
            "permission_scope": candidate.permission_scope,
        }


__all__ = ["MemoryCandidateApplication", "MemoryCandidateInput", "MemoryPromotionError", "PromotedMemory"]
