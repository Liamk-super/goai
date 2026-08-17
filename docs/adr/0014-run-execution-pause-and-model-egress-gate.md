# ADR-0014: Run-scoped execution pause and model egress gate

## Status

Accepted

## Context

Closing the review UI does not currently establish a durable execution boundary. AgentTeams and CoPaw can continue periodic or task-driven model calls after the browser leaves, while terminating shared Workers would also terminate unrelated or future evaluations. The released Run lifecycle contract has no resumable pause state and must remain immutable.

The control plane must guarantee that a confirmed pause admits no new paid model or tool calls, preserves completed work, permits one already-submitted call to settle, and resumes only the affected Run after an explicit user action. PostgreSQL remains the business-state source of truth. Unknown submission, usage, billing, or external side effects must continue to fail closed.

## Decision

Run lifecycle and execution control are orthogonal. The released Run status vocabulary is unchanged. A tenant-scoped `run_execution_control` row records `ACTIVE`, `PAUSE_REQUESTED`, `PAUSED`, `PAUSE_BLOCKED`, or `CLOSED`, with a monotonically increasing control epoch.

Pause and model admission serialize on the execution-control row. Every model request is registered before external submission by an internal OpenAI-compatible egress gateway. Once pause commits, only invocations registered before that transaction may settle; subsequent model and MCP/tool requests are rejected locally. Unknown settlement transitions the execution control to `PAUSE_BLOCKED` and the Run to `NEEDS_ATTENTION` without retry or failover.

Unsubmitted task-ready Outbox rows are held. Active deliveries are interrupted only by a room-scoped Matrix stop command and checkpointed; shared Manager and Worker processes remain running. Resume requires an explicit REST command with the current epoch. It unholds unchanged tasks and redispatches only interrupted incomplete tasks with a new dispatch epoch and capability.

AgentTeams heartbeat execution is disabled. Formal evaluations remain event driven through durable Outbox and Matrix assignments.

## Consequences

### Positive

- Paused Runs cannot admit hidden background model or tool work.
- Completed tasks, immutable evidence, budget reservations, and usage remain attributable and resumable.
- A paused Run cannot stop or preempt another Run using the shared Worker pool.
- Provider uncertainty remains fail closed and auditable.

### Negative

- Model traffic must traverse an additional internal streaming service.
- The interrupted task may require a new delivery after resume when no durable handoff was committed.
- Pause completion can remain pending while one admitted invocation settles.

### Neutral

- Browser close remains a warning-only action because it cannot reliably commit a server-side transition.
- Existing terminal Runs are backfilled as `CLOSED`; existing non-terminal Runs are backfilled as `ACTIVE`.

## Alternatives Considered

**Add `PAUSED` to released RunStatus**

Rejected because it would mutate a published lifecycle contract and couple business outcome to execution admission.

**Stop AgentTeams containers or revoke the provider key**

Rejected because the action is global and would terminate unrelated or future evaluations.

**Send `/stop` without an egress gate**

Rejected because transport cancellation races with model submission and cannot prove that no later paid call was admitted.

**Treat browser unload as pause**

Rejected because unload delivery is unreliable and would create a false safety claim.

## References

- ADR-0006: Agent runtime context, accounting, and deadlines
- ADR-0012: Supervisor 1+4-only admission and Agent reports
- `docs/runbooks/unknown-submission.md`

