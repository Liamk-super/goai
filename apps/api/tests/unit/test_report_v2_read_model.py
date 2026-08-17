from __future__ import annotations

import hashlib
import json
from typing import Any, cast
from uuid import uuid4

import pytest

from launchscope_api.modules.experience.api import _load_report_v2
from launchscope_api.modules.project_dossier.material_ingestion import ObjectMetadata
from launchscope_api.modules.user_validation.application import ArtifactIntegrityError


class _Objects:
    def __init__(self, body: bytes, *, observed_sha256: str | None = None) -> None:
        self.body = body
        self.observed_sha256 = observed_sha256 or hashlib.sha256(body).hexdigest()

    def head(self, _key: str) -> ObjectMetadata:
        return ObjectMetadata(
            sha256=self.observed_sha256,
            size_bytes=len(self.body),
            mime_type="application/json",
            etag="v2",
            metadata={},
        )

    def get_private(self, _key: str, *, max_bytes: int) -> bytes:
        assert max_bytes == 2_000_000
        return self.body


def _supervisor_document() -> dict[str, Any]:
    run_id, report_id, project_id, version_id = (uuid4() for _ in range(4))
    agent_codes = ["user-evidence", "product-engineering", "business-investment", "evidence-auditor"]
    return {
        "schema_version": "2.0",
        "report_id": str(report_id),
        "run_id": str(run_id),
        "project_id": str(project_id),
        "product_version_id": str(version_id),
        "product_title": "Evidence product",
        "source_sha256": "a" * 64,
        "top_card": {
            "potential_index": 68,
            "stage": "早期验证",
            "confidence_band": "MEDIUM",
            "evidence_coverage": 0.5,
            "recommendation": "VALIDATE_FURTHER",
        },
        "summary_claim_id": "claim-summary",
        "claims": [
            {
                "claim_id": "claim-summary",
                "section": "HIGHLIGHT",
                "text": "关键主张待验证",
                "status": "PENDING_VALIDATION",
                "decision_relevance": "CONTEXT",
                "citation_ids": [],
                "score_bearing": False,
            }
        ],
        "highlights": ["claim-summary"],
        "critical_issues": [],
        "role_summaries": {"user": [], "product": [], "investment": []},
        "cross_domain_claims": [],
        "actions": [
            {
                "action_id": "action-validate",
                "title": "补充验证",
                "owner": "项目负责人",
                "deadline_days": 14,
                "success_criteria": ["取得可审计证据"],
                "failure_triggers": ["证据无法复现"],
                "required_evidence": ["访谈记录"],
                "related_claim_ids": ["claim-summary"],
            }
        ],
        "confidence_breakdown": {
            "profile_ref": "confidence@1.0",
            "audited_evidence_quality": 0.5,
            "evidence_coverage": 0.5,
            "independent_source_support": 0.5,
            "freshness": 0.5,
            "cross_domain_agreement": 0.5,
            "unresolved_conflict_penalty": 0.1,
            "score": 0.5,
            "band": "MEDIUM",
        },
        "agent_report_cards": [
            {
                "agent_code": code,
                "report_id": str(uuid4()),
                "title": f"{code} report",
                "summary_claim_ids": ["claim-summary"],
                "source_sha256": "b" * 64,
            }
            for code in agent_codes
        ],
        "citations": [],
        "source_directory": [],
        "audit_detail_ref": "evidence-auditor",
    }


def test_v2_supervisor_read_returns_the_hash_verified_canonical_document() -> None:
    document = _supervisor_document()
    body = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    metadata = {
        "report_id": document["report_id"],
        "run_id": document["run_id"],
        "object_key": "private/report-v2.json",
        "sha256": hashlib.sha256(body).hexdigest(),
        "created_at": "2026-08-13T00:00:00+00:00",
    }

    result = _load_report_v2(metadata, kind="SUPERVISOR", object_store=cast(Any, _Objects(body)))

    assert result["document"] == document
    assert result["integrity"]["canonical_sha256"] == metadata["sha256"]
    assert len(result["document"]["agent_report_cards"]) == 4
    assert "comparison" not in result["document"]


def test_v2_supervisor_read_fails_closed_on_catalog_hash_mismatch() -> None:
    document = _supervisor_document()
    body = json.dumps(document, separators=(",", ":")).encode()
    metadata = {
        "report_id": document["report_id"],
        "run_id": document["run_id"],
        "object_key": "private/report-v2.json",
        "sha256": hashlib.sha256(body).hexdigest(),
        "created_at": "2026-08-13T00:00:00+00:00",
    }

    with pytest.raises(ArtifactIntegrityError, match="durable catalog"):
        _load_report_v2(
            metadata,
            kind="SUPERVISOR",
            object_store=cast(Any, _Objects(body, observed_sha256="f" * 64)),
        )

