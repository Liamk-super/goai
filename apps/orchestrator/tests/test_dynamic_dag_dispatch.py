from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from launchscope_api.modules.evaluation.planning_application import DynamicDagDispatchValidator
from launchscope_domain import BudgetReservation, EvaluationRun, StageCode, Task, TaskCompletion, TenantScope
from launchscope_orchestrator.harness import HarnessSpec, RunHarness
from launchscope_orchestrator.manifest_loader import AgentManifestLoader


def _run_with_manifest() -> EvaluationRun:
    version_id = uuid4()
    scope = TenantScope(tenant_id=uuid4(), workspace_id=uuid4(), project_id=uuid4(), product_version_id=version_id)
    run = EvaluationRun.create(scope, version_id)
    run.start_intake().mark_material_profile_complete()
    budget = BudgetReservation(run.run_id, "tool", 10, 10)
    spec = HarnessSpec(
        product_version_id=version_id,
        material_hashes={uuid4(): "a" * 64},
        standard_version="1.0",
        prompt_versions={"manager": "1.0"},
        model_versions={"planner": "1.0"},
        tool_versions={"browser.read.v1": "1.0"},
        budget_limits=(budget,),
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
            "product-intake-normalizer": ("material_hash",), "intake-gap-diagnosis": ("profile_provenance",),
            "browser-product-audit": ("private_snapshot_hash",),
            "business-investment-assessment": ("source_url_or_material_hash",),
            "evidence-grounding-audit": ("finding_evidence_chain",),
            "version-regression-verification": ("same_standard_version",),
        },
    )
    run.reserve_budget((budget,))
    RunHarness().freeze_for_run(run, spec)
    return run


def _browser_task(run: EvaluationRun, *, dependencies=(), tools=("browser.read.v1",), timeout_seconds=120) -> Task:
    return Task(
        task_id=uuid4(),
        run_id=run.run_id,
        stage_code=StageCode.PARALLEL_EVALUATION,
        agent_identity_ref="product-engineering@1.0",
        skill_ref="browser-product-audit",
        skill_version="1.0",
        dependencies=dependencies,
        tool_allowlist=tools,
        budget_slice=BudgetReservation(run.run_id, "tool", 5, 5),
        timeout_seconds=timeout_seconds,
        success_condition="validated browser audit result",
        evidence_requirement="private_snapshot_hash",
        evidence_required=True,
    )


def test_dispatch_requires_frozen_identity_skill_tool_budget_and_evidence_contract() -> None:
    run = _run_with_manifest()
    task = _browser_task(run)
    run.add_task(task)

    decision = DynamicDagDispatchValidator(AgentManifestLoader().load_all()).validate(run, task)

    assert decision.allowed


def test_dispatch_rejects_unsatisfied_dependency_and_unfrozen_tool() -> None:
    run = _run_with_manifest()
    first = _browser_task(run)
    dependent = _browser_task(run, dependencies=(first.task_id,), tools=("repository.read.v1",))
    run.add_task(first)
    run.add_task(dependent)
    validator = DynamicDagDispatchValidator(AgentManifestLoader().load_all())

    assert validator.validate(run, dependent).code == "DEPENDENCY_NOT_READY"
    first.lease("lease").start_running().succeed(TaskCompletion(evidence_ids=(uuid4(),)))
    assert validator.validate(run, dependent).code == "TOOL_NOT_FROZEN"
