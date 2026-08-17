from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest

from launchscope_api.modules.supervisor.baseline_application import (
    BaselineCandidate,
    baseline_status,
    bind_baseline_once,
    content_fingerprint_sha256,
    input_snapshot_sha256,
    report_profile_ref,
    report_v3_enabled,
)


@pytest.mark.parametrize(
    ("prior_status", "same_content", "standard_changed", "expected"),
    [
        (None, False, False, "FIRST_EVALUATION"),
        ("COMPLETED", False, False, "COMPARABLE"),
        ("COMPLETED", False, True, "STANDARD_CHANGED"),
        ("COMPLETED", True, False, "SAME_INPUT_RERUN"),
        ("FAILED", False, False, "FIRST_EVALUATION"),
    ],
)
def test_baseline_selection_decision_table(
    prior_status: str | None,
    same_content: bool,
    standard_changed: bool,
    expected: str,
) -> None:
    assert (
        baseline_status(prior_status, same_content=same_content, standards_compatible=not standard_changed) == expected
    )


def test_input_hash_preserves_identity_while_content_fingerprint_ignores_random_ids() -> None:
    document = {
        "project_id": str(uuid4()),
        "product_version_id": str(uuid4()),
        "confirmed_product_profile": {"target_user": "Independent retailers", "region": "HK"},
        "material_selection": {
            "selection_id": str(uuid4()),
            "sha256": "a" * 64,
            "included_materials": [
                {"material_id": str(uuid4()), "sha256": "c" * 64},
                {"material_id": str(uuid4()), "sha256": "b" * 64},
            ],
        },
        "user_validation_script": {"script_id": str(uuid4()), "sha256": "d" * 64},
        "evaluation_mode": "FULL_POTENTIAL",
    }
    same_content_new_identity = deepcopy(document)
    same_content_new_identity["project_id"] = str(uuid4())
    same_content_new_identity["product_version_id"] = str(uuid4())
    same_content_new_identity["material_selection"]["selection_id"] = str(uuid4())
    same_content_new_identity["material_selection"]["included_materials"].reverse()
    for item in same_content_new_identity["material_selection"]["included_materials"]:
        item["material_id"] = str(uuid4())
    same_content_new_identity["user_validation_script"]["script_id"] = str(uuid4())

    assert input_snapshot_sha256(document) != input_snapshot_sha256(same_content_new_identity)
    assert content_fingerprint_sha256(document) == content_fingerprint_sha256(same_content_new_identity)


def test_content_fingerprint_changes_when_confirmed_product_content_changes() -> None:
    document = {
        "project_id": str(uuid4()),
        "product_version_id": str(uuid4()),
        "confirmed_product_profile": {"target_user": "Independent retailers", "region": "HK"},
        "included_material_sha256s": ["a" * 64],
        "evaluation_mode": "FULL_POTENTIAL",
    }
    changed = deepcopy(document)
    changed["confirmed_product_profile"]["region"] = "Singapore"
    assert content_fingerprint_sha256(document) != content_fingerprint_sha256(changed)


def test_later_run_does_not_replace_an_already_bound_baseline() -> None:
    original = BaselineCandidate(
        run_id=uuid4(),
        status="COMPLETED",
        input_snapshot_sha256="a" * 64,
        content_fingerprint_sha256="b" * 64,
        standard_version="2.2",
        report_profile_ref="supervisor-report@2.0",
    )
    later = BaselineCandidate(
        run_id=uuid4(),
        status="COMPLETED",
        input_snapshot_sha256="c" * 64,
        content_fingerprint_sha256="d" * 64,
        standard_version="2.2",
        report_profile_ref="supervisor-report@2.0",
    )
    assert bind_baseline_once(original.run_id, later) == original.run_id
    assert bind_baseline_once(None, original) == original.run_id


def test_v3_feature_flag_selects_a_new_report_profile_without_changing_the_standard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_REPORT_V3_ENABLED", "true")
    monkeypatch.setenv("LAUNCHSCOPE_REPORT_V2_ENABLED", "true")

    assert report_v3_enabled()
    assert report_profile_ref() == "supervisor-report@3.0"
