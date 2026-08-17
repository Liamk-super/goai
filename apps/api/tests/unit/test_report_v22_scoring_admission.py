from __future__ import annotations

import json
from pathlib import Path

from launchscope_api.modules.supervisor.completion_application import DeterministicScoringEngine

ROOT = Path(__file__).resolve().parents[4]


def _profile() -> dict[str, object]:
    return json.loads(
        (ROOT / "packages/contracts/score/profiles/full-potential.v2.json").read_text(encoding="utf-8")
    )


def _tasks() -> list[dict[str, object]]:
    return [
        {"agent": agent, "required": True, "status": "SUCCEEDED"}
        for agent in ("user-evidence", "product-engineering", "business-investment")
    ]


def _finding(
    agent: str,
    *,
    decision: str = "ACCEPTED",
    score_bearing: bool,
    citation_status: str,
    freshness_status: str = "VALID",
    score: float = 5,
) -> dict[str, object]:
    return {
        "finding": {"agent_code": agent, "score_input": score},
        "audit_decision": decision,
        "score_bearing": score_bearing,
        "citation_status": citation_status,
        "freshness_status": freshness_status,
        "support_strength": "MODERATE" if score_bearing else "NONE",
        "independent_source_count": 1 if score_bearing else 0,
    }


def test_unsupported_score_input_is_ignored() -> None:
    result = DeterministicScoringEngine().score(
        _profile(),
        [
            _finding(
                "user-evidence",
                decision="REJECTED",
                score_bearing=False,
                citation_status="REJECTED",
            ),
            _finding(
                "product-engineering",
                score_bearing=True,
                citation_status="VERIFIED",
            ),
            _finding(
                "business-investment",
                score_bearing=True,
                citation_status="VERIFIED",
            ),
        ],
        _tasks(),
        unresolved_conflicts=False,
    )

    assert result.dimension_scores["user_value"] is None
    assert result.coverage == 0.6667
    assert result.score < 100


def test_expired_evidence_is_not_score_bearing_under_v2_profile() -> None:
    result = DeterministicScoringEngine().score(
        _profile(),
        [
            _finding(
                "user-evidence",
                score_bearing=False,
                citation_status="PENDING_VALIDATION",
                freshness_status="EXPIRED",
            ),
            _finding(
                "product-engineering",
                score_bearing=True,
                citation_status="VERIFIED",
            ),
            _finding(
                "business-investment",
                score_bearing=True,
                citation_status="VERIFIED",
            ),
        ],
        _tasks(),
        unresolved_conflicts=False,
    )

    assert result.dimension_scores["user_value"] is None
    assert result.coverage == 0.6667


def test_downgraded_claim_scores_only_with_valid_citation_support() -> None:
    result = DeterministicScoringEngine().score(
        _profile(),
        [
            _finding(
                "user-evidence",
                decision="DOWNGRADED",
                score_bearing=True,
                citation_status="DOWNGRADED",
            ),
            _finding(
                "product-engineering",
                decision="DOWNGRADED",
                score_bearing=False,
                citation_status="PENDING_VALIDATION",
            ),
            _finding(
                "business-investment",
                score_bearing=True,
                citation_status="VERIFIED",
            ),
        ],
        _tasks(),
        unresolved_conflicts=False,
    )

    assert result.dimension_scores["user_value"] == 75
    assert result.dimension_scores["product_capability"] is None
    assert result.dimension_scores["investment_potential"] == 100
    assert result.coverage == 0.6667
