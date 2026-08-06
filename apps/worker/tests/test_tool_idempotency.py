from __future__ import annotations

from uuid import uuid4

from launchscope_domain import BudgetReservation, RunManifest, StageCode, Task, TaskStatus
from launchscope_worker.runtime.dispatch import WorkerDispatcher
from launchscope_worker.tool_gateway.contract import AdapterResult, ToolInvocationStatus


def _manifest(run_id):
    return RunManifest(
        tool_versions={"repository.read.v1": "1.0"},
        permissions=("repository.read",),
        budget_limits=(BudgetReservation(run_id, "tool", 1, 1),),
        timeout_seconds=60,
    ).freeze()


def _task(run_id):
    return Task(
        task_id=uuid4(),
        run_id=run_id,
        stage_code=StageCode.PARALLEL_EVALUATION,
        tool_allowlist=("repository.read.v1",),
        budget_slice=BudgetReservation(run_id, "tool", 1, 1),
        timeout_seconds=30,
    )


def test_duplicate_idempotency_key_does_not_submit_twice() -> None:
    run_id, calls = uuid4(), []
    task = _task(run_id)
    manifest = _manifest(run_id)
    dispatcher = WorkerDispatcher()

    def adapter(params, contract):
        calls.append(params)
        return AdapterResult({"path": "README.md", "sha256": "a" * 64, "content": "read-only"})

    first = dispatcher.dispatch(
        run_id=run_id,
        task=task,
        manifest=manifest,
        worker_id="worker",
        tool_id="repository.read.v1",
        parameters={"path": "README.md"},
        adapter=adapter,
    )
    again = dispatcher.dispatch(
        run_id=run_id,
        task=task,
        manifest=manifest,
        worker_id="worker",
        tool_id="repository.read.v1",
        parameters={"path": "README.md"},
        adapter=adapter,
    )
    assert first.invocation_id == again.invocation_id
    assert len(calls) == 1


def test_unknown_submission_or_cost_stops_at_needs_attention_without_retry() -> None:
    run_id, requests = uuid4(), []
    task = _task(run_id)
    invocation = WorkerDispatcher(state_sink=requests.append).dispatch(
        run_id=run_id,
        task=task,
        manifest=_manifest(run_id),
        worker_id="worker",
        tool_id="repository.read.v1",
        parameters={"path": "README.md"},
        adapter=lambda params, contract: AdapterResult({}, submission_state_known=False),
    )
    assert invocation.status is ToolInvocationStatus.NEEDS_ATTENTION
    assert invocation.failure_class.value == "SUBMISSION_UNKNOWN"
    assert requests[0].requested_status is TaskStatus.NEEDS_ATTENTION
