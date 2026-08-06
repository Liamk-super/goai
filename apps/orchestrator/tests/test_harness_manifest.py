from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from launchscope_domain import AppendOnlyViolation, BudgetReservation, EvaluationRun, TenantScope
from launchscope_orchestrator.harness import HarnessSpec, HarnessValidationError, RunHarness


def _spec(run_id, product_version_id) -> HarnessSpec:
    return HarnessSpec(
        product_version_id=product_version_id,
        material_hashes={uuid4(): "a" * 64},
        standard_version="1.0",
        prompt_versions={"manager": "1.0"},
        model_versions={"planner": "1.0"},
        tool_versions={
            "browser.read.v1": "1.0",
            "public-research.get.v1": "1.0",
            "repository.read.v1": "1.0",
        },
        budget_limits=(BudgetReservation(run_id, "tool", 10, 10),),
        permissions=(
            "browser.read",
            "evidence.read",
            "finding.read",
            "material.read",
            "profile.read",
            "public_research.read",
        ),
        timeout_seconds=300,
        regions=("HK",),
        data_as_of=datetime(2026, 8, 5, tzinfo=UTC),
        approval_points=("external_action",),
        failure_policy={
            "TRANSIENT": "FAIL",
            "VALIDATION": "FAIL",
            "AUTHORIZATION": "NEEDS_ATTENTION",
            "DEPENDENCY": "FAIL",
            "BUDGET": "NEEDS_ATTENTION",
            "POLICY": "NEEDS_ATTENTION",
            "SUBMISSION_UNKNOWN": "NEEDS_ATTENTION",
            "BUSINESS": "FAIL",
        },
        evidence_requirements={
            "product-intake-normalizer": ("material_hash",),
            "intake-gap-diagnosis": ("profile_provenance",),
            "browser-product-audit": ("private_snapshot_hash",),
            "business-investment-assessment": ("source_url_or_material_hash",),
            "evidence-grounding-audit": ("finding_evidence_chain",),
            "version-regression-verification": ("same_standard_version",),
        },
    )


def test_harness_freezes_complete_reproducible_manifest_and_rejects_replacement() -> None:
    version_id = uuid4()
    scope = TenantScope(tenant_id=uuid4(), workspace_id=uuid4(), project_id=uuid4(), product_version_id=version_id)
    run = EvaluationRun.create(scope, version_id)
    run.start_intake().mark_material_profile_complete()
    spec = _spec(run.run_id, version_id)
    run.reserve_budget(spec.budget_limits)

    manifest = RunHarness().freeze_for_run(run, spec)

    assert manifest.frozen
    assert len(manifest.skill_versions) == 6
    assert len(manifest.configuration["material_hashes"]) == 1
    assert len(manifest.configuration["agent_contract_hashes"]) == 6
    with pytest.raises(TypeError):
        manifest.configuration["x"] = "not allowed"  # type: ignore[index]
    with pytest.raises(TypeError):
        manifest.configuration["material_hashes"]["x"] = "not allowed"  # type: ignore[index]
    with pytest.raises(AppendOnlyViolation):
        run.freeze_manifest(RunHarness().build_manifest(_spec(run.run_id, version_id), run_id=run.run_id))


def test_harness_rejects_unknown_submission_retry_policy() -> None:
    run_id, version_id = uuid4(), uuid4()
    spec = _spec(run_id, version_id)
    unsafe = replace(spec, failure_policy={**spec.failure_policy, "SUBMISSION_UNKNOWN": "RETRY"})

    with pytest.raises(HarnessValidationError, match="fail closed"):
        RunHarness().build_manifest(unsafe, run_id=run_id)
