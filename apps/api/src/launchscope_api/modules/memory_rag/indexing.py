"""PostgreSQL-backed hybrid retrieval with an auditable retrieval record."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from launchscope_api.infrastructure.db.schema import finding_evidence, memory_item, rag_retrieval

from .retrieval_policy import RetrievalPolicy, RetrievalScope

_TOKENS = re.compile(r"[\w-]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    memory_id: UUID
    source_finding_id: UUID | None
    evidence_ids: tuple[UUID, ...]
    score: float
    content: dict[str, object]


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    retrieval_id: UUID
    result_hash: str
    filters: dict[str, object]
    hits: tuple[RetrievalHit, ...]


class MemoryRagIndex:
    """Ranks only rows already admitted by :class:`RetrievalPolicy`.

    Embedding storage is intentionally optional in V0.1: the policy boundary
    is identical whether a deployment enables pgvector ranking or uses the
    deterministic lexical scorer below.
    """

    def __init__(self, session: Session, policy: RetrievalPolicy | None = None) -> None:
        self.session = session
        self.policy = policy or RetrievalPolicy()

    def retrieve(self, request: RetrievalScope, query: str, *, limit: int = 20) -> RetrievalResult:
        if not query.strip() or not 1 <= limit <= 100:
            raise ValueError("query must be non-empty and limit must be between 1 and 100")
        rows = self.session.execute(select(memory_item).where(*self.policy.predicates(request))).mappings().all()
        terms = set(_TOKENS.findall(query.casefold()))
        allowed_rows = [dict(row) for row in rows if self.policy.permits_row(request, dict(row))]
        finding_ids = {row["source_finding_id"] for row in allowed_rows if row["source_finding_id"] is not None}
        evidence_by_finding: dict[UUID, tuple[UUID, ...]] = {}
        if finding_ids:
            links = self.session.execute(
                select(finding_evidence.c.finding_id, finding_evidence.c.evidence_id).where(
                    finding_evidence.c.tenant_id == request.scope.tenant_id,
                    finding_evidence.c.finding_id.in_(finding_ids),
                )
            ).all()
            for finding_id, evidence_id in links:
                evidence_by_finding[finding_id] = (*evidence_by_finding.get(finding_id, ()), evidence_id)
        ranked = sorted(
            (
                RetrievalHit(
                    memory_id=row["id"],
                    source_finding_id=row["source_finding_id"],
                    evidence_ids=evidence_by_finding.get(row["source_finding_id"], ()),
                    score=self._lexical_score(terms, row.get("search_text", "")),
                    content=dict(row["content"]),
                )
                for row in allowed_rows
            ),
            key=lambda item: (-item.score, str(item.memory_id)),
        )[:limit]
        filters: dict[str, object] = {
            "tenant_id": str(request.scope.tenant_id),
            "project_id": str(request.scope.project_id),
            "product_version_id": str(request.scope.product_version_id),
            "region": request.region,
            "permissions": sorted(request.permissions),
            "at": request.at.isoformat(),
        }
        result_hash = self._hash(filters, ranked)
        retrieval_id = uuid4()
        self.session.execute(
            insert(rag_retrieval).values(
                id=retrieval_id,
                tenant_id=request.scope.tenant_id,
                project_id=request.scope.project_id,
                product_version_id=request.scope.product_version_id,
                run_id=request.scope.run_id,
                query_sha256=hashlib.sha256(query.encode("utf-8")).hexdigest(),
                filters=filters,
                hit_memory_ids=[str(hit.memory_id) for hit in ranked],
                hit_finding_ids=[str(hit.source_finding_id) for hit in ranked if hit.source_finding_id],
                hit_evidence_ids=[str(evidence_id) for hit in ranked for evidence_id in hit.evidence_ids],
                result_sha256=result_hash,
                created_at=datetime.now(UTC),
            )
        )
        return RetrievalResult(retrieval_id=retrieval_id, result_hash=result_hash, filters=filters, hits=tuple(ranked))

    @staticmethod
    def _lexical_score(terms: set[str], text: object) -> float:
        tokens = set(_TOKENS.findall(str(text).casefold()))
        return float(len(terms & tokens))

    @staticmethod
    def _hash(filters: dict[str, object], hits: list[RetrievalHit]) -> str:
        payload = {
            "filters": filters,
            "hits": [
                {
                    "memory_id": str(hit.memory_id),
                    "finding_id": str(hit.source_finding_id),
                    "evidence_ids": [str(value) for value in hit.evidence_ids],
                    "score": hit.score,
                }
                for hit in hits
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


__all__ = ["MemoryRagIndex", "RetrievalHit", "RetrievalResult"]
