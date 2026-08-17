from __future__ import annotations

import json
from pathlib import Path

from launchscope_api.modules.supervisor.completion_application import (
    DeterministicScoringEngine,
    VersionChangeComparator,
)

ROOT = Path(__file__).resolve().parents[4]


def _profile(name: str = "full-potential"):
    return json.loads(
        (ROOT / f"packages/contracts/score/profiles/{name}.v1.json").read_text(encoding="utf-8")
    )


def _finding(agent: str, score: float, audit_decision: str = "ACCEPTED"):
    return {
        "finding": {"agent_code": agent, "score_input": score},
        "audit_decision": audit_decision,
    }


def test_scoring_is_deterministic_and_rejected_findings_add_no_positive_score() -> None:
    findings = [
        _finding("user-evidence", 5),
        _finding("product-engineering", 5),
        _finding("business-investment", 5, "REJECTED"),
    ]
    tasks = [
        {"agent": "user-evidence", "status": "SUCCEEDED", "required": True},
        {"agent": "product-engineering", "status": "SUCCEEDED", "required": True},
        {"agent": "business-investment", "status": "SUCCEEDED", "required": True},
    ]
    first = DeterministicScoringEngine().score(_profile(), findings, tasks, unresolved_conflicts=False)
    second = DeterministicScoringEngine().score(_profile(), findings, tasks, unresolved_conflicts=False)
    assert first == second
    assert first.dimension_scores["investment_potential"] is None
    assert first.coverage == 0.6667
    assert "low_coverage:VALIDATE_FURTHER" in first.caps_applied


def test_optional_failure_and_unresolved_conflict_apply_versioned_caps() -> None:
    profile = _profile("investment-review")
    findings = [_finding("business-investment", 5), _finding("product-engineering", 5)]
    tasks = [
        {"agent": "business-investment", "status": "SUCCEEDED", "required": True},
        {"agent": "product-engineering", "status": "SUCCEEDED", "required": False},
        {"agent": "user-evidence", "status": "KNOWN_FAILED", "required": False},
    ]
    result = DeterministicScoringEngine().score(profile, findings, tasks, unresolved_conflicts=True)
    assert result.recommendation != "PROCEED"
    assert "missing_optional_agent:VALIDATE_FURTHER" in result.caps_applied
    assert "unresolved_conflict:VALIDATE_FURTHER" in result.caps_applied


def test_reevaluation_compares_only_after_current_findings_are_independently_scored() -> None:
    previous = [
        {
            "finding": {
                "agent_code": "user-evidence",
                "score_input": 2,
                "claim": "existing user signal",
                "grade": "WEAK",
            },
            "audit_decision": "ACCEPTED",
        }
    ]
    current = [
        {
            "finding": {
                "agent_code": "user-evidence",
                "score_input": 4,
                "claim": "existing user signal",
                "grade": "STRONG",
            },
            "audit_decision": "ACCEPTED",
        },
        {
            "finding": {
                "agent_code": "product-engineering",
                "score_input": 1,
                "claim": "new delivery risk",
                "grade": "WEAK",
            },
            "audit_decision": "ACCEPTED",
        },
    ]
    changes = VersionChangeComparator().compare(previous, current)
    assert changes == {
        "improved": ["user-evidence"],
        "unchanged": [],
        "new_risks": ["new delivery risk"],
    }
