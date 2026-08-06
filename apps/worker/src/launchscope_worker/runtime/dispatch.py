"""Bridge frozen Harness dispatch facts into the isolated Worker boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from launchscope_domain import FailureClass, RunManifest, Task, TaskStatus

from ..tool_gateway.contract import ToolAdapter, ToolGateway, ToolInvocation, ToolInvocationStatus
from .lease import LeaseRegistry


@dataclass(frozen=True, slots=True)
class WorkerStateChangeRequest:
    task_id: UUID
    requested_status: TaskStatus
    failure_class: FailureClass
    reason: str
    source: str = "isolated-worker"


StateChangeSink = Callable[[WorkerStateChangeRequest], None]


class WorkerDispatcher:
    """Uses no ambient permissions: each call derives its authority from Harness."""

    def __init__(
        self,
        *,
        gateway: ToolGateway | None = None,
        leases: LeaseRegistry | None = None,
        state_sink: StateChangeSink | None = None,
    ) -> None:
        self.leases = leases or LeaseRegistry()
        self.gateway = gateway or ToolGateway(leases=self.leases)
        self.state_sink = state_sink or (lambda request: None)

    def dispatch(
        self,
        *,
        run_id: UUID,
        task: Task,
        manifest: RunManifest,
        worker_id: str,
        tool_id: str,
        parameters: Mapping[str, object],
        adapter: ToolAdapter,
    ) -> ToolInvocation:
        if not manifest.frozen or task.run_id != run_id:
            raise ValueError("Worker dispatch requires the matching frozen RunManifest and Task")
        idempotency_key = f"{task.idempotency_key}:{tool_id}"
        cached = self.gateway.lookup(run_id, task.task_id, tool_id, idempotency_key)
        if cached is not None:
            return cached
        lease = self.leases.acquire(
            task.task_id, worker_id, ttl_seconds=min(task.timeout_seconds, manifest.timeout_seconds)
        )
        budget = task.budget_slice.reserved if task.budget_slice is not None else 0
        invocation = self.gateway.invoke(
            run_id=run_id,
            task_id=task.task_id,
            task_tools=task.tool_allowlist,
            task_timeout_seconds=task.timeout_seconds,
            task_budget=float(budget),
            manifest=manifest,
            lease_token=lease.token,
            tool_id=tool_id,
            idempotency_key=idempotency_key,
            parameters=parameters,
            adapter=adapter,
        )
        if invocation.status is ToolInvocationStatus.NEEDS_ATTENTION:
            self.state_sink(
                WorkerStateChangeRequest(
                    task.task_id,
                    TaskStatus.NEEDS_ATTENTION,
                    FailureClass.SUBMISSION_UNKNOWN,
                    invocation.reason or "unknown Tool submission state",
                )
            )
        return invocation
