from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

from .locale_validation import generated_locale_matches

_ROOT = Path(__file__).resolve().parents[6]
_DIMENSIONS = ("user_value", "product_capability", "investment_potential", "evidence_quality")
_POLARITY_FIELDS = {
    "POSITIVE": "positive_driver_claim_ids",
    "NEGATIVE": "negative_driver_claim_ids",
    "PENDING": "pending_validation_claim_ids",
}


class SupervisorReportV3Error(ValueError):
    pass


class SupervisorReportV3Builder:
    def __init__(self) -> None:
        self._synthesis_schema = json.loads(
            (_ROOT / "packages/contracts/manager/manager-synthesis.v3.json").read_text(encoding="utf-8")
        )
        self._report_schema = json.loads(
            (_ROOT / "packages/contracts/reports/supervisor-report.v3.json").read_text(encoding="utf-8")
        )

    def build(
        self,
        *,
        report_id: UUID,
        run: dict[str, Any],
        decision: dict[str, Any],
        synthesis: dict[str, Any],
        driver_claims: list[dict[str, Any]],
        citations: list[dict[str, Any]],
        source_directory: list[dict[str, Any]],
        agent_report_cards: list[dict[str, Any]],
        allowed_evidence_ids: set[UUID],
        source_sha256: str,
        audit_detail_ref: str,
    ) -> dict[str, Any]:
        self._validate(self._synthesis_schema, synthesis, "ManagerSynthesisV3")
        locale = str(run.get("locale") or "").strip()
        if locale not in {"zh-CN", "en"} or synthesis["locale"] != locale:
            raise SupervisorReportV3Error("the frozen Run and ManagerSynthesisV3 locale must match")
        product_title = str(run.get("product_title") or "").strip()
        if not product_title:
            raise SupervisorReportV3Error("the target Run must provide its persisted product title")
        if UUID(str(synthesis["run_id"])) != UUID(str(run["id"])):
            raise SupervisorReportV3Error("ManagerSynthesisV3 targets a different Run")
        if UUID(str(synthesis["deterministic_decision_ref"])) != UUID(str(decision["id"])):
            raise SupervisorReportV3Error("ManagerSynthesisV3 targets a different Decision")
        generated_prose = [
            *[item["text"] for item in synthesis["claims"]],
            *[
                {
                    "title": item["title"],
                    "owner": item["owner"],
                    "success_criteria": item["success_criteria"],
                    "failure_triggers": item["failure_triggers"],
                    "required_evidence": item["required_evidence"],
                }
                for item in synthesis["actions"]
            ],
        ]
        if not generated_locale_matches(locale, generated_prose):
            raise SupervisorReportV3Error("generated report prose does not match the frozen locale")

        claims = copy.deepcopy(synthesis["claims"])
        claim_by_id = {str(item["claim_id"]): item for item in claims}
        normalized_drivers: list[dict[str, Any]] = []
        for item in driver_claims:
            if any(key in item for key in ("value", "score", "recommendation", "confidence")):
                raise SupervisorReportV3Error("a report driver cannot override an authoritative dimension value")
            dimension = str(item.get("dimension"))
            polarity = str(item.get("polarity"))
            claim = copy.deepcopy(item.get("claim"))
            if dimension not in _DIMENSIONS or polarity not in _POLARITY_FIELDS or not isinstance(claim, dict):
                raise SupervisorReportV3Error("dimension driver metadata is invalid")
            claim_id = str(claim.get("claim_id"))
            if claim_id in claim_by_id and claim_by_id[claim_id] != claim:
                raise SupervisorReportV3Error("dimension driver Claim conflicts with Manager synthesis")
            if claim_id not in claim_by_id:
                claims.append(claim)
                claim_by_id[claim_id] = claim
            normalized_drivers.append({"dimension": dimension, "polarity": polarity, "claim_id": claim_id})

        citation_by_id = {str(item["citation_id"]): item for item in citations}
        if len(citation_by_id) != len(citations):
            raise SupervisorReportV3Error("Citation identifiers must be unique")
        locator_by_id = {str(item["source_locator_id"]): item for item in source_directory}
        for citation in citations:
            claim_id = str(citation["claim_id"])
            if claim_id not in claim_by_id:
                raise SupervisorReportV3Error("Citation refers to an unknown Claim")
            if UUID(str(citation["evidence_id"])) not in allowed_evidence_ids:
                raise SupervisorReportV3Error("Citation refers to Evidence outside the Run")
            locator_id = citation.get("source_locator_id")
            if locator_id is not None and str(locator_id) not in locator_by_id:
                raise SupervisorReportV3Error("Citation refers to an unknown source locator")
        for claim in claims:
            attached: list[dict[str, Any]] = []
            for citation_id in claim["citation_ids"]:
                citation = citation_by_id.get(str(citation_id))
                if citation is None:
                    raise SupervisorReportV3Error("Claim refers to an unknown Citation")
                if citation["claim_id"] != claim["claim_id"]:
                    raise SupervisorReportV3Error("Citation is bound to a different Claim")
                attached.append(citation)
            self._validate_claim_strength(claim, attached)
        for action in synthesis["actions"]:
            if not set(action["related_claim_ids"]).issubset(claim_by_id):
                raise SupervisorReportV3Error("Action refers to an unknown Claim")

        dimension_grades = dict(decision.get("dimension_grades") or {})
        authoritative_scores = dict(dimension_grades.get("dimension_scores") or {})
        if set(authoritative_scores) != set(_DIMENSIONS):
            raise SupervisorReportV3Error("the authoritative decision must provide exactly four dimension values")
        dimension_scores = self._dimension_scores(authoritative_scores, normalized_drivers, claim_by_id)
        confidence = copy.deepcopy(dimension_grades.get("confidence_breakdown"))
        if not isinstance(confidence, dict):
            raise SupervisorReportV3Error("authoritative conclusion confidence is missing")
        comparison = copy.deepcopy(dimension_grades.get("comparison"))
        evidence_coverage = float(dimension_grades["evidence_coverage"])
        covered_dimensions = min(len(_DIMENSIONS), max(0, round(evidence_coverage * len(_DIMENSIONS))))

        document: dict[str, Any] = {
            "schema_version": "3.0",
            "locale": locale,
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
                "evidence_coverage": evidence_coverage,
                "recommendation": str(decision["recommendation"]),
            },
            "dimension_scores": dimension_scores,
            "evidence_coverage_profile": {
                "definition_version": "evidence-coverage@2.0",
                "label": "EVIDENCE_COVERAGE",
                "required_dimensions": len(_DIMENSIONS),
                "covered_dimensions": covered_dimensions,
                "quality_note": (
                    "证据质量单独计入结论可信度，不等同于覆盖数量。"
                    if locale == "zh-CN"
                    else "Evidence quality contributes to confidence separately from coverage quantity."
                ),
                "independent_support_note": (
                    "独立来源支持单独计算；重复转载不增加独立性。"
                    if locale == "zh-CN"
                    else "Independent support is measured separately; syndicated copies do not add independence."
                ),
            },
            "summary_claim_id": str(synthesis["summary_claim_id"]),
            "claims": claims,
            "highlights": self._section_ids(claims, "HIGHLIGHT"),
            "critical_issues": self._section_ids(claims, "CRITICAL_ISSUE"),
            "issue_priorities": self._issue_priorities(claims, locale),
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
            "source_directory": self._visible_sources(source_directory, citations),
            "audit_detail_ref": audit_detail_ref,
        }
        if isinstance(comparison, dict) and comparison.get("status") in {"COMPARABLE", "STANDARD_CHANGED"}:
            document["comparison"] = comparison
        if document["summary_claim_id"] not in claim_by_id:
            raise SupervisorReportV3Error("summary_claim_id refers to an unknown Claim")
        self._validate(self._report_schema, document, "SupervisorReportDocumentV3")
        return document

    @staticmethod
    def _validate_claim_strength(claim: dict[str, Any], citations: list[dict[str, Any]]) -> None:
        supports = [item for item in citations if item["support_role"] == "SUPPORT"]
        status = str(claim["status"])
        if status == "VERIFIED" and not any(item["audit_status"] == "VERIFIED" for item in supports):
            raise SupervisorReportV3Error("Claim strength is stronger than its supporting Citation strength")
        if status == "DOWNGRADED" and not any(item["audit_status"] in {"VERIFIED", "DOWNGRADED"} for item in supports):
            raise SupervisorReportV3Error("Claim strength is stronger than its supporting Citation strength")
        if status in {"PENDING_VALIDATION", "CONFLICTED"} and claim["score_bearing"]:
            raise SupervisorReportV3Error("pending or conflicted Claims cannot be score-bearing")

    @staticmethod
    def _dimension_scores(
        authoritative_scores: dict[str, Any],
        drivers: list[dict[str, Any]],
        claim_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        grouped = {
            dimension: {
                "positive_driver_claim_ids": [],
                "negative_driver_claim_ids": [],
                "pending_validation_claim_ids": [],
            }
            for dimension in _DIMENSIONS
        }
        for driver in drivers:
            grouped[driver["dimension"]][_POLARITY_FIELDS[driver["polarity"]]].append(driver["claim_id"])
        result: dict[str, dict[str, Any]] = {}
        for dimension in _DIMENSIONS:
            claim_ids = [claim_id for values in grouped[dimension].values() for claim_id in values]
            if not claim_ids:
                raise SupervisorReportV3Error(f"dimension {dimension} has no score driver or validation gap")
            statuses = {str(claim_by_id[claim_id]["status"]) for claim_id in claim_ids}
            if "VERIFIED" in statuses:
                strength, evidence_level = "STRONG", "HIGH"
            elif "DOWNGRADED" in statuses:
                strength, evidence_level = "MODERATE", "MEDIUM"
            elif "CONFLICTED" in statuses:
                strength, evidence_level = "WEAK", "LOW"
            else:
                strength, evidence_level = "INSUFFICIENT_EVIDENCE", "PENDING"
            result[dimension] = {
                "value": authoritative_scores[dimension],
                "strength": strength,
                "evidence_level": evidence_level,
                **grouped[dimension],
            }
        return result

    @staticmethod
    def _issue_priorities(claims: list[dict[str, Any]], locale: str) -> list[dict[str, str]]:
        candidates = [item for item in claims if item["section"] in {"CRITICAL_ISSUE", "INFORMATION_GAP"}]
        rank = {"CRITICAL": 0, "IMPORTANT": 1, "CONTEXT": 2}
        candidates.sort(key=lambda item: (rank[str(item["decision_relevance"])], str(item["claim_id"])))
        priorities: list[dict[str, str]] = []
        for item in candidates[:3]:
            status = str(item["status"])
            relevance = str(item["decision_relevance"])
            if item["section"] == "CRITICAL_ISSUE" and relevance == "CRITICAL" and status != "PENDING_VALIDATION":
                priority = "P0"
                impact = (
                    "会改变是否继续投入的判断" if locale == "zh-CN" else "May change whether investment should continue"
                )
            elif relevance in {"CRITICAL", "IMPORTANT"}:
                priority = "P1"
                impact = (
                    "明显影响成功可能性，需要本轮优先验证"
                    if locale == "zh-CN"
                    else "Materially affects viability and needs priority validation"
                )
            else:
                priority = "P2"
                impact = (
                    "属于优化项，不扩大精简报告首屏"
                    if locale == "zh-CN"
                    else "An optimization item outside the concise first screen"
                )
            priorities.append({"priority": priority, "claim_id": str(item["claim_id"]), "decision_impact": impact})
        return priorities

    @staticmethod
    def _visible_sources(
        source_directory: list[dict[str, Any]], citations: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        cited = {
            str(item["source_locator_id"])
            for item in citations
            if item.get("source_locator_id") is not None and item["audit_status"] in {"VERIFIED", "DOWNGRADED"}
        }
        unique: dict[str, dict[str, Any]] = {}
        for source in source_directory:
            if str(source["source_locator_id"]) not in cited:
                continue
            key = str(source.get("canonical_url") or "").strip().lower()
            if not key:
                key = f"{source['source_kind']}:{str(source['title']).strip().lower()}:{source['content_sha256']}"
            unique.setdefault(key, copy.deepcopy(source))
        return list(unique.values())

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
            raise SupervisorReportV3Error(f"{label} contract violation at {errors[0].json_path}: {errors[0].message}")


__all__ = ["SupervisorReportV3Builder", "SupervisorReportV3Error"]
