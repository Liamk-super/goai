# ADR-0017: Delivery-scoped model admission and single-posting usage ledger

## Status

Accepted

## Context

ADR-0014 introduced Run-scoped pause and a local model egress gate. The generation-v4 AgentTeams topology now runs
multiple domain deliveries in parallel, while each physical CoPaw Worker is reused across Runs. A credential scoped
only to an Agent role cannot distinguish a late request from an old delivery after the same Worker has moved to a new
Run. The egress gateway and the terminal Matrix handoff also both persist model usage, which can post the same provider
work twice.

The control plane must preserve the existing 1+4 workflow and its bounded parallelism, reject stale Worker traffic,
settle every externally submitted invocation exactly once, and post each delivery's financial usage exactly once.
PostgreSQL remains the business-state source of truth. Provider, submission, delivery, usage, or billing uncertainty
must fail closed without automatic retry or model replacement.

## Decision

Every model admission is bound to the immutable tuple `(tenant_id, run_id, task_id, delivery_id, dispatch_epoch,
control_epoch, agent_code)`. A global physical-Worker lease carries a short-lived delivery capability. A Worker may
have only one `PREPARING`, `ACTIVE`, or `DRAINING` lease across all tenants. Delivery completion, pause, or timeout
revokes that capability before the Worker can be reused.

`model_invocation` is the canonical per-request submission and settlement ledger. Financial settlement and downstream
delivery are recorded as independent states. The gateway records invocation facts and budget holds but does not post
`usage_record` rows or consume the Run reservation. Terminal handoff, pause, or timeout reconciliation aggregates all
invocations for one delivery and posts one idempotent Task receipt. CoPaw cumulative counters remain independent
reconciliation evidence; they are not a second financial posting source.

Runs freeze either `COPAW_TASK_DELTA` or `GATEWAY_DELIVERY` accounting in their manifest. Existing Runs retain their
original mode. The current Run call, token, and budget ceilings and per-Agent iteration limits remain in force. Each
delivery additionally allows at most `max_iters + 4` admitted calls and one in-flight invocation.

Pause preserves parallel execution: after the pause transaction commits, no new invocation is admitted, but every
invocation submitted before the transaction may settle. The public in-flight count reports that exact number rather
than claiming a fixed single call.

This decision supplements ADR-0014 and supersedes only the final financial-posting responsibility described by
ADR-0006 and ADR-0011 for Runs using `GATEWAY_DELIVERY` mode. Published Run and handoff contracts are unchanged.

## Consequences

### Positive

- Late traffic from a previous delivery cannot be charged to a new Run using the same Worker.
- The gateway and Matrix handoff cannot double-post model Token, call, or cost usage.
- Run pause remains resumable without globally serializing the 1+4 workflow.
- Unknown submission or delivery stays visible and cannot trigger a duplicate paid call.

### Negative

- Worker configuration becomes part of the durable dispatch handshake.
- A failed Matrix send can leave a `PREPARING` lease that requires fail-closed reconciliation.
- CoPaw telemetry becomes advisory for new Runs and can raise reconciliation alerts.

## Alternatives considered

**Keep role-scoped credentials**

Rejected because a stale CoPaw session can be attributed to the next active Task for that role.

**Use gateway usage and CoPaw deltas as two financial ledgers**

Rejected because the same provider work can consume the Run budget twice.

**Serialize all model calls in a Run**

Rejected because it would remove the intended parallel domain review and extend the full-flow deadline solely to make
pause accounting simpler.

**Stop shared Workers on pause**

Rejected because it can terminate unrelated Runs and does not prove the provider submission boundary.

## References

- `docs/adr/0006-agent-runtime-context-accounting-and-deadlines.md`
- `docs/adr/0011-token-only-provider-cost-accounting.md`
- `docs/adr/0014-run-execution-pause-and-model-egress-gate.md`

