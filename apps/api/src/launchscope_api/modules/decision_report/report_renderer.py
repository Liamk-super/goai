"""Render a report as a compact summary plus an expandable evidence chain."""

from __future__ import annotations

import html
from collections.abc import Mapping
from dataclasses import dataclass

from launchscope_domain.aggregates.decision_report import Decision, Report
from launchscope_domain.aggregates.evidence_review import EvidenceReview


@dataclass(frozen=True, slots=True)
class EvidenceChainNode:
    report_id: str
    decision_id: str
    finding_id: str
    evidence_id: str
    object_key: str
    sha256: str
    source_type: str
    trust_level: str


@dataclass(frozen=True, slots=True)
class RenderedReport:
    report_id: str
    summary: Mapping[str, object]
    evidence_chain: tuple[EvidenceChainNode, ...]
    html: str


class ReportRenderer:
    """Keeps the evidence chain explicit; no model text decides the outcome."""

    def render(self, report: Report, decision: Decision, review: EvidenceReview) -> RenderedReport:
        if report.decision_id != decision.decision_id:
            raise ValueError("report does not belong to the supplied decision")
        chain: list[EvidenceChainNode] = []
        for finding_id in decision.finding_ids:
            finding = review.findings.get(finding_id)
            if finding is None:
                continue
            for evidence_id in finding.evidence_ids:
                evidence = review.evidence.get(evidence_id)
                if evidence is None:
                    continue
                chain.append(
                    EvidenceChainNode(
                        report_id=str(report.report_id),
                        decision_id=str(decision.decision_id),
                        finding_id=str(finding_id),
                        evidence_id=str(evidence_id),
                        object_key=evidence.ref.object_key,
                        sha256=evidence.ref.sha256,
                        source_type=str(evidence.ref.source_type),
                        trust_level=str(evidence.ref.trust_level),
                    )
                )
        summary = {
            "stage": "SYNTHESIS",
            "recommendation": decision.recommendation.value,
            "standard_version": decision.standard_version,
            "dimension_grades": {key.value: value.value for key, value in decision.dimension_grades.items()},
            "blocking_reasons": list(decision.blocking_reasons),
            "key_tensions": list(decision.blocking_reasons),
            "largest_risk": decision.blocking_reasons[0] if decision.blocking_reasons else "no_rule_block_recorded",
            "largest_opportunity": self._largest_opportunity(decision),
            "action_items": list(report.action_items),
            "information_gaps": [reason for reason in decision.blocking_reasons if "evidence" in reason],
            "version_changes": (
                [f"supersedes_report:{report.supersedes_id}"] if report.supersedes_id is not None else []
            ),
        }
        return RenderedReport(
            report_id=str(report.report_id),
            summary=summary,
            evidence_chain=tuple(chain),
            html=self._render_html(summary, chain),
        )

    @staticmethod
    def _largest_opportunity(decision: Decision) -> str:
        for dimension, grade in decision.dimension_grades.items():
            if grade.value in {"STRONG", "MODERATE"}:
                return dimension.value
        return "no_supported_opportunity"

    @staticmethod
    def _render_html(summary: Mapping[str, object], chain: list[EvidenceChainNode]) -> str:
        dimension_grades = summary["dimension_grades"]
        if not isinstance(dimension_grades, Mapping):
            raise ValueError("rendered report requires dimension grades")
        grades = "".join(
            f"<li>{html.escape(str(key))}: {html.escape(str(value))}</li>" for key, value in dimension_grades.items()
        )
        chain_html = "".join(
            "<li><details><summary>Finding "
            + html.escape(item.finding_id)
            + " / Evidence "
            + html.escape(item.evidence_id)
            + "</summary><code>"
            + html.escape(item.object_key)
            + "</code><br>sha256="
            + html.escape(item.sha256)
            + "</details></li>"
            for item in chain
        )
        return (
            "<article><h1>LaunchScope report</h1><p>Recommendation: "
            + html.escape(str(summary["recommendation"]))
            + "</p><h2>Four dimensions</h2><ul>"
            + grades
            + "</ul><h2>Evidence chain</h2><ul>"
            + chain_html
            + "</ul></article>"
        )


__all__ = ["EvidenceChainNode", "RenderedReport", "ReportRenderer"]
