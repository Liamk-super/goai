from __future__ import annotations

import json
from pathlib import Path

from launchscope_api.modules.supervisor.report_metrics import (
    compute_conclusion_confidence,
    compute_evidence_coverage,
    confidence_band,
)

ROOT = Path(__file__).resolve().parents[4]


def _profile() -> dict[str, object]:
    return json.loads(
        (ROOT / "packages/contracts/score/profiles/full-potential.v2.json").read_text(encoding="utf-8")
    )


def _audit(
    agent: str,
    *,
    score_bearing: bool = True,
    support_strength: str = "MODERATE",
    independent_source_count: int = 1,
    freshness_score: float = 1,
) -> dict[str, object]:
    return {
        "finding": {"agent_code": agent, "hypothesis": False},
        "audit_decision": "ACCEPTED" if score_bearing else "REJECTED",
        "score_bearing": score_bearing,
        "citation_status": "VERIFIED" if score_bearing else "REJECTED",
        "support_strength": support_strength,
        "independent_source_count": independent_source_count,
        "freshness_status": "VALID" if freshness_score == 1 else "NEAR_EXPIRY",
        "freshness_score": freshness_score,
    }


def test_evidence_coverage_uses_required_dimension_weights() -> None:
    coverage = compute_evidence_coverage(
        _profile(),
        [
            _audit("user-evidence"),
            _audit("product-engineering"),
            _audit("business-investment", score_bearing=False),
        ],
    )

    assert coverage == 0.7


def test_conclusion_confidence_uses_only_recomputable_components() -> None:
    result = compute_conclusion_confidence(
        _profile(),
        [
            _audit("user-evidence", support_strength="MODERATE", independent_source_count=2),
            _audit(
                "product-engineering",
                support_strength="WEAK",
                freshness_score=0.5,
            ),
        ],
        evidence_coverage=0.7,
        cross_domain_agreement=0.8,
        unresolved_conflicts=False,
        profile_ref="score-profile:full-potential@2.0",
    )

    assert result == {
        "profile_ref": "score-profile:full-potential@2.0",
        "audited_evidence_quality": 0.575,
        "evidence_coverage": 0.7,
        "independent_source_support": 0.75,
        "freshness": 0.75,
        "cross_domain_agreement": 0.8,
        "unresolved_conflict_penalty": 0.0,
        "score": 0.6813,
        "band": "MEDIUM",
    }


def test_confidence_bands_use_exact_profile_thresholds() -> None:
    profile = _profile()["confidence_profile"]

    assert confidence_band(0.4499, profile) == "LOW"
    assert confidence_band(0.45, profile) == "MEDIUM"
    assert confidence_band(0.7499, profile) == "MEDIUM"
    assert confidence_band(0.75, profile) == "HIGH"
