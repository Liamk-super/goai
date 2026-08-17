from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

_ROOT = Path(__file__).resolve().parents[6]


class SupervisorReportV2Error(ValueError):
    pass


class SupervisorReportV2Builder:
    def __init__(self) -> None:
        self._synthesis_schema = json.loads(
            (_ROOT / "packages/contracts/manager/manager-synthesis.v2.json").read_text(encoding="utf-8")
        )
        self._report_schema = json.loads(
            (_ROOT / "packages/contracts/reports/supervisor-report.v2.json").read_text(encoding="utf-8")
        )

    def build(
        self,
        *,
        report_id: UUID,
        run: dict[str, Any],
        decision: dict[str, Any],
        synthesis: dict[str, Any],
        citations: list[dict[str, Any]],
        source_directory: list[dict[str, Any]],
        agent_report_cards: list[dict[str, Any]],
        allowed_evidence_ids: set[UUID],
        source_sha256: str,
        audit_detail_ref: str,
    ) -> dict[str, Any]:
        self._validate(self._synthesis_schema, synthesis, "ManagerSynthesisV2")
        product_title = str(run.get("product_title") or "").strip()
        if not product_title:
            raise SupervisorReportV2Error("the target Run must provide its persisted product title")
        if UUID(str(synthesis["run_id"])) != UUID(str(run["id"])):
            raise SupervisorReportV2Error("ManagerSynthesisV2 targets a different Run")
        if UUID(str(synthesis["deterministic_decision_ref"])) != UUID(str(decision["id"])):
            raise SupervisorReportV2Error("ManagerSynthesisV2 targets a different Decision")

        claims = copy.deepcopy(synthesis["claims"])
        claim_by_id = {str(item["claim_id"]): item for item in claims}
        citation_by_id = {str(item["citation_id"]): item for item in citations}
        if len(citation_by_id) != len(citations):
            raise SupervisorReportV2Error("Citation identifiers must be unique")
        locator_ids = {str(item["source_locator_id"]) for item in source_directory}
        for citation in citations:
            claim_id = str(citation["claim_id"])
            if claim_id not in claim_by_id:
                raise SupervisorReportV2Error("Citation refers to an unknown Claim")
            if UUID(str(citation["evidence_id"])) not in allowed_evidence_ids:
                raise SupervisorReportV2Error("Citation refers to Evidence outside the Run")
            locator_id = citation.get("source_locator_id")
            if locator_id is not None and str(locator_id) not in locator_ids:
                raise SupervisorReportV2Error("Citation refers to an unknown source locator")
        for claim in claims:
            attached = []
            for citation_id in claim["citation_ids"]:
                attached_citation = citation_by_id.get(str(citation_id))
                if attached_citation is None:
                    raise SupervisorReportV2Error("Claim refers to an unknown Citation")
                if attached_citation["claim_id"] != claim["claim_id"]:
                    raise SupervisorReportV2Error("Citation is bound to a different Claim")
                attached.append(attached_citation)
            if claim["status"] == "VERIFIED" and (
                not attached or all(item["audit_status"] in {"REJECTED", "NEEDS_MORE"} for item in attached)
            ):
                raise SupervisorReportV2Error("verified Claim is backed only by rejected Evidence")
        for action in synthesis["actions"]:
            if not set(action["related_claim_ids"]).issubset(claim_by_id):
                raise SupervisorReportV2Error("Action refers to an unknown Claim")

        dimension_grades = dict(decision["dimension_grades"])
        confidence = copy.deepcopy(dimension_grades.get("confidence_breakdown"))
        comparison: dict[str, Any] | None = copy.deepcopy(dimension_grades.get("comparison"))
        if confidence is None:
            raise SupervisorReportV2Error("authoritative conclusion confidence is missing")
        document: dict[str, Any] = {
            "schema_version": "2.0",
            "report_id": str(report_id),
            "run_id": str(run["id"]),
            "project_id": str(run["project_id"]),
            "product_version_id": str(run["product_version_id"]),
            "product_title": product_title,
            "source_sha256": source_sha256,
            "top_card": {
                "potential_index": float(dimension_grades["score"]),
                "stage": str(run["stage"]),
                "confidence_band": confidence["band"],
                "evidence_coverage": float(dimension_grades["evidence_coverage"]),
                "recommendation": str(decision["recommendation"]),
            },
            "summary_claim_id": str(synthesis["summary_claim_id"]),
            "claims": claims,
            "highlights": self._section_ids(claims, "HIGHLIGHT"),
            "critical_issues": self._section_ids(claims, "CRITICAL_ISSUE"),
            "role_summaries": {
                "user": self._section_ids(claims, "USER"),
                "product": self._section_ids(claims, "PRODUCT"),
                "investment": self._section_ids(claims, "INVESTMENT"),
            },
            "cross_domain_claims": self._section_ids(claims, "CROSS_DOMAIN"),
            "actions": copy.deepcopy(synthesis["actions"]),
            "confidence_breakdown": confidence,
            "agent_report_cards": copy.deepcopy(agent_report_cards),
            "citations": copy.deepcopy(citations),
            "source_directory": copy.deepcopy(source_directory),
            "audit_detail_ref": audit_detail_ref,
        }
        if comparison and comparison.get("status") in {"COMPARABLE", "STANDARD_CHANGED"}:
            document["comparison"] = comparison
        if document["summary_claim_id"] not in claim_by_id:
            raise SupervisorReportV2Error("summary_claim_id refers to an unknown Claim")
        self._validate(self._report_schema, document, "SupervisorReportDocumentV2")
        return document

    @staticmethod
    def _section_ids(claims: list[dict[str, Any]], section: str) -> list[str]:
        return [str(item["claim_id"]) for item in claims if item["section"] == section]

    @staticmethod
    def _validate(schema: dict[str, Any], document: dict[str, Any], label: str) -> None:
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
            key=lambda item: item.json_path,
        )
        if errors:
            raise SupervisorReportV2Error(f"{label} contract violation at {errors[0].json_path}")


__all__ = ["SupervisorReportV2Builder", "SupervisorReportV2Error"]
