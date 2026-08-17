from __future__ import annotations

from typing import Any

_SUPPORT_RANK = {"NONE": 0, "WEAK": 1, "MODERATE": 2, "STRONG": 3}


def _risks(state: dict[str, Any]) -> set[str]:
    return {
        str(item["claim_id"])
        for item in state.get("findings", [])
        if item.get("risk")
        or item.get("audit_decision") in {"REJECTED", "NEEDS_MORE"}
        or item.get("citation_status") in {"PENDING_VALIDATION", "REJECTED"}
    }


def _finding_map(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["claim_id"]): item for item in state.get("findings", [])}


def _compatible(candidate: dict[str, Any], prior: dict[str, Any]) -> bool:
    return all(
        candidate.get(key) == prior.get(key)
        for key in (
            "standard_version",
            "score_profile_ref",
            "score_profile_sha256",
            "report_profile_ref",
            "required_dimension_set",
        )
    )


def _identity_fields(candidate: dict[str, Any], prior: dict[str, Any] | None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": "1.0",
        "candidate_run_id": str(candidate["run_id"]),
        "candidate_input_snapshot_sha256": str(candidate["input_snapshot_sha256"]),
        "candidate_score_profile_ref": str(candidate["score_profile_ref"]),
        "candidate_report_profile_ref": str(candidate["report_profile_ref"]),
    }
    if candidate.get("report_id") is not None:
        document["candidate_report_id"] = str(candidate["report_id"])
    if prior is not None:
        document.update(
            {
                "prior_run_id": str(prior["run_id"]),
                "prior_input_snapshot_sha256": str(prior["input_snapshot_sha256"]),
                "prior_score_profile_ref": str(prior["score_profile_ref"]),
                "prior_report_profile_ref": str(prior["report_profile_ref"]),
            }
        )
        if prior.get("report_id") is not None:
            document["prior_report_id"] = str(prior["report_id"])
    return document


def build_report_comparison(candidate: dict[str, Any], prior: dict[str, Any] | None) -> dict[str, Any]:
    document = _identity_fields(candidate, prior)
    empty_changes: dict[str, list[str]] = {
        "resolved_issues": [],
        "unchanged_issues": [],
        "new_risks": [],
        "evidence_upgrades": [],
        "evidence_downgrades": [],
        "change_reason_claim_ids": [],
    }
    if prior is None:
        return {**document, "status": "FIRST_EVALUATION", **empty_changes}
    if candidate.get("content_fingerprint_sha256") == prior.get("content_fingerprint_sha256"):
        return {**document, "status": "SAME_INPUT_RERUN", **empty_changes}

    prior_risks = _risks(prior)
    candidate_risks = _risks(candidate)
    prior_findings = _finding_map(prior)
    candidate_findings = _finding_map(candidate)
    shared = set(prior_findings).intersection(candidate_findings)
    upgrades = sorted(
        claim_id
        for claim_id in shared
        if _SUPPORT_RANK.get(str(candidate_findings[claim_id].get("support_strength", "NONE")), 0)
        > _SUPPORT_RANK.get(str(prior_findings[claim_id].get("support_strength", "NONE")), 0)
    )
    downgrades = sorted(
        claim_id
        for claim_id in shared
        if _SUPPORT_RANK.get(str(candidate_findings[claim_id].get("support_strength", "NONE")), 0)
        < _SUPPORT_RANK.get(str(prior_findings[claim_id].get("support_strength", "NONE")), 0)
    )
    resolved = sorted(prior_risks.difference(candidate_risks))
    unchanged = sorted(prior_risks.intersection(candidate_risks))
    new_risks = sorted(candidate_risks.difference(prior_risks))
    change_reasons = sorted(set(resolved + new_risks + upgrades + downgrades))
    document.update(
        {
            "resolved_issues": resolved,
            "unchanged_issues": unchanged,
            "new_risks": new_risks,
            "evidence_upgrades": upgrades,
            "evidence_downgrades": downgrades,
            "change_reason_claim_ids": change_reasons,
        }
    )
    if not _compatible(candidate, prior):
        document["status"] = "STANDARD_CHANGED"
        return document

    before = float(prior["potential_index"])
    after = float(candidate["potential_index"])
    prior_dimensions = prior.get("dimension_scores", {})
    candidate_dimensions = candidate.get("dimension_scores", {})
    dimensions = sorted(set(prior_dimensions).intersection(candidate_dimensions))
    document.update(
        {
            "status": "COMPARABLE",
            "index_before": before,
            "index_after": after,
            "index_delta": round(after - before, 2),
            "dimension_deltas": [
                {
                    "dimension": dimension,
                    "before": float(prior_dimensions[dimension]),
                    "after": float(candidate_dimensions[dimension]),
                    "delta": round(float(candidate_dimensions[dimension]) - float(prior_dimensions[dimension]), 2),
                }
                for dimension in dimensions
                if prior_dimensions[dimension] is not None and candidate_dimensions[dimension] is not None
            ],
        }
    )
    return document


__all__ = ["build_report_comparison"]
