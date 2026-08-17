from __future__ import annotations

from typing import Any

_DIMENSION_BY_AGENT = {
    "user-evidence": "user_value",
    "product-engineering": "product_capability",
    "business-investment": "investment_potential",
}
_SUPPORT_QUALITY = {"NONE": 0.0, "WEAK": 0.4, "MODERATE": 0.75, "STRONG": 1.0}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _is_admitted(item: dict[str, Any]) -> bool:
    source = item.get("finding") or {}
    return (
        item.get("audit_decision") in {"ACCEPTED", "DOWNGRADED"}
        and item.get("citation_status") in {"VERIFIED", "DOWNGRADED"}
        and bool(item.get("score_bearing"))
        and item.get("freshness_status") not in {"EXPIRED", "SUPERSEDED"}
        and not bool(source.get("hypothesis"))
    )


def compute_evidence_coverage(profile: dict[str, Any], audited_findings: list[dict[str, Any]]) -> float:
    weights = profile["coverage_rules"]["required_dimension_weights"]
    covered = {
        _DIMENSION_BY_AGENT.get(str(item.get("finding", {}).get("agent_code")))
        for item in audited_findings
        if _is_admitted(item)
    }
    if any(_is_admitted(item) for item in audited_findings):
        covered.add("evidence_quality")
    denominator = sum(float(weights.get(dimension, 0)) for dimension in profile["required_dimensions"])
    if denominator <= 0:
        return 0.0
    numerator = sum(float(weights.get(dimension, 0)) for dimension in covered if dimension is not None)
    return round(_clamp(numerator / denominator), 4)


def confidence_band(score: float, confidence_profile: dict[str, Any]) -> str:
    thresholds = confidence_profile["band_thresholds"]
    if score >= float(thresholds["HIGH"]):
        return "HIGH"
    if score >= float(thresholds["MEDIUM"]):
        return "MEDIUM"
    return "LOW"


def compute_conclusion_confidence(
    profile: dict[str, Any],
    audited_findings: list[dict[str, Any]],
    *,
    evidence_coverage: float,
    cross_domain_agreement: float,
    unresolved_conflicts: bool,
    profile_ref: str,
) -> dict[str, Any]:
    if audited_findings:
        audited_quality = sum(
            _SUPPORT_QUALITY.get(str(item.get("support_strength", "NONE")), 0.0)
            if _is_admitted(item)
            else 0.0
            for item in audited_findings
        ) / len(audited_findings)
        independent_support = sum(
            min(float(item.get("independent_source_count", 0)) / 2, 1.0)
            if _is_admitted(item)
            else 0.0
            for item in audited_findings
        ) / len(audited_findings)
        freshness = sum(
            _clamp(float(item.get("freshness_score", 0))) for item in audited_findings
        ) / len(audited_findings)
    else:
        audited_quality = independent_support = freshness = 0.0
    confidence_profile = profile["confidence_profile"]
    components = {
        "audited_evidence_quality": _clamp(audited_quality),
        "evidence_coverage": _clamp(evidence_coverage),
        "independent_source_support": _clamp(independent_support),
        "freshness": _clamp(freshness),
        "cross_domain_agreement": _clamp(cross_domain_agreement),
    }
    weights = confidence_profile["component_weights"]
    raw_score = sum(components[key] * float(weights[key]) for key in components)
    penalty = float(confidence_profile["unresolved_conflict_penalty"]) if unresolved_conflicts else 0.0
    score = _clamp(raw_score - penalty)
    document = {
        "profile_ref": profile_ref,
        **{key: round(value, 4) for key, value in components.items()},
        "unresolved_conflict_penalty": round(penalty, 4),
        "score": round(score, 4),
        "band": confidence_band(score, confidence_profile),
    }
    return document


__all__ = ["compute_evidence_coverage", "compute_conclusion_confidence", "confidence_band"]
