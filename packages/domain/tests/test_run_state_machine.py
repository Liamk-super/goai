from __future__ import annotations

from uuid import UUID

import pytest

from launchscope_domain import (
    BudgetReservation,
    EvaluationRun,
    FailureClass,
    InvalidTransitionError,
    RunManifest,
    RunStateMachine,
    RunStatus,
    StageCode,
    Task,
    TaskCompletion,
    TenantScope,
)


def test_illegal_run_transition_returns_structured_error() -> None:
    check = RunStateMachine.check(RunStatus.DRAFT, RunStatus.RUNNING)
    assert not check.allowed
    assert check.code == "ILLEGAL_TRANSITION"
    assert check.current is RunStatus.DRAFT
    assert check.target is RunStatus.RUNNING


def test_run_cannot_start_without_frozen_manifest_and_budget(scope: TenantScope) -> None:
    run = EvaluationRun.create(scope, scope.product_version_id or UUID(int=1))
    run.start_intake()
    run.mark_material_profile_complete()
    with pytest.raises(InvalidTransitionError) as error:
        run.start_execution()
    assert error.value.code == "INVALID_STATE_TRANSITION"
    assert run.status is RunStatus.PLANNED


def test_full_run_flow_requires_task_terminal_evidence_audit_and_commit(scope: TenantScope) -> None:
    version_id = scope.product_version_id or UUID(int=1)
    run = EvaluationRun.create(scope, version_id)
    run.start_intake().mark_material_profile_complete()
    run.reserve_budget((BudgetReservation(run.run_id, "model", 100, 100),))
    run.freeze_manifest(RunManifest(standard_version="1.0"))
    run.start_execution()

    task = Task.create(run.run_id, StageCode.PARALLEL_EVALUATION, evidence_required=True)
    run.add_task(task)
    task.lease("lease-1").start_running().succeed(
        TaskCompletion(evidence_ids=(UUID("60000000-0000-4000-8000-000000000001"),))
    )
    run.mark_required_tasks_terminal().enter_evidence_review()
    with pytest.raises(InvalidTransitionError):
        run.begin_synthesis()
    run.mark_audit_ready().begin_synthesis()
    run.mark_decision_committed().mark_report_committed().mark_dossier_committed().complete()
    assert run.status is RunStatus.COMPLETED


def test_unknown_submission_freezes_run_until_reconciliation(scope: TenantScope) -> None:
    version_id = scope.product_version_id or UUID(int=1)
    run = EvaluationRun.create(scope, version_id)
    run.start_intake().mark_material_profile_complete()
    run.reserve_budget((BudgetReservation(run.run_id, "tool", 1, 1),))
    run.freeze_manifest(RunManifest(standard_version="1.0"))
    run.start_execution().mark_needs_attention(FailureClass.SUBMISSION_UNKNOWN, "provider status unknown")

    assert run.status is RunStatus.NEEDS_ATTENTION
    assert run.retry_blocked
    with pytest.raises(InvalidTransitionError):
        run.resume()
    run.resume(reconciliation_complete=True)
    assert run.status is RunStatus.RUNNING
