from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker

from launchscope_api.modules.supervisor.report_comparison import build_report_comparison

ROOT = Path(__file__).resolve().parents[4]
SHA_A = "a" * 64
SHA_B = "b" * 64


def _state(*, fingerprint: str, standard: str = "v2.2", score: float = 60) -> dict[str, object]:
    return {
        "run_id": str(uuid4()),
        "report_id": str(uuid4()),
        "input_snapshot_sha256": fingerprint,
        "content_fingerprint_sha256": fingerprint,
        "standard_version": standard,
        "score_profile_ref": "score-profile:full-potential@2.0",
        "score_profile_sha256": SHA_A,
        "report_profile_ref": "supervisor-report@2.0",
        "required_dimension_set": "full-potential-v2",
        "potential_index": score,
        "dimension_scores": {"user_value": 50, "product_capability": 60},
        "findings": [
            {"claim_id": "claim-risk", "risk": True, "support_strength": "WEAK"},
            {"claim_id": "claim-still", "risk": True, "support_strength": "MODERATE"},
        ],
    }


def _assert_valid(document: dict[str, object]) -> None:
    schema = json.loads(
        (ROOT / "packages/contracts/reports/report-comparison.v1.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)


def test_comparable_reports_include_deltas_and_identifier_based_risk_changes() -> None:
    prior = _state(fingerprint=SHA_A, score=60)
    current = _state(fingerprint=SHA_B, score=68)
    current["dimension_scores"] = {"user_value": 55, "product_capability": 60}
    current["findings"] = [
        {"claim_id": "claim-risk", "risk": False, "support_strength": "STRONG"},
        {"claim_id": "claim-still", "risk": True, "support_strength": "MODERATE"},
        {"claim_id": "claim-new", "risk": True, "support_strength": "WEAK"},
    ]

    document = build_report_comparison(current, prior)

    _assert_valid(document)
    assert document["status"] == "COMPARABLE"
    assert document["index_delta"] == 8
    assert document["dimension_deltas"] == [
        {"dimension": "product_capability", "before": 60.0, "after": 60.0, "delta": 0.0},
        {"dimension": "user_value", "before": 50.0, "after": 55.0, "delta": 5.0},
    ]
    assert document["resolved_issues"] == ["claim-risk"]
    assert document["unchanged_issues"] == ["claim-still"]
    assert document["new_risks"] == ["claim-new"]
    assert document["evidence_upgrades"] == ["claim-risk"]


def test_standard_change_never_exposes_index_or_dimension_deltas() -> None:
    prior = _state(fingerprint=SHA_A, standard="v2.1", score=25)
    current = _state(fingerprint=SHA_B, standard="v2.2", score=90)

    document = build_report_comparison(current, prior)

    _assert_valid(document)
    assert document["status"] == "STANDARD_CHANGED"
    assert not {"index_before", "index_after", "index_delta", "dimension_deltas"}.intersection(document)


def test_same_content_rerun_points_to_immediate_prior_without_change_claims() -> None:
    prior = _state(fingerprint=SHA_A)
    current = _state(fingerprint=SHA_A, score=99)

    document = build_report_comparison(current, prior)

    _assert_valid(document)
    assert document["status"] == "SAME_INPUT_RERUN"
    assert document["prior_run_id"] == prior["run_id"]
    assert document["resolved_issues"] == []
    assert "index_delta" not in document


def test_first_evaluation_has_no_prior_or_delta_fields() -> None:
    document = build_report_comparison(_state(fingerprint=SHA_A), None)

    _assert_valid(document)
    assert document["status"] == "FIRST_EVALUATION"
    assert not any(key.startswith("prior_") for key in document)
