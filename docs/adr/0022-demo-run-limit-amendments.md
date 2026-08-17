# ADR 0022: Append-only Demo Run limit amendments

- Status: Accepted
- Date: 2026-08-15
- Decision owners: LaunchScope product owner and evaluation control plane
- Amends: ADR 0011 token-only accounting and ADR 0019 Demo force recovery
- Does not change: immutable Run Manifests, PostgreSQL authority, external-submission fail-closed policy, or production admission

## Context

The authorized CreaTrades Demo Run retained one Run identity through several recoveries. Those attempts consumed real,
known model calls and Tokens. A later successful Agent result was rejected because the cumulative usage exceeded the
limits frozen at admission. Editing the historical Run Manifest, deleting usage, ignoring the limit in `TOKEN_ONLY`,
or creating a replacement formal Run would each violate an existing product or architecture boundary.

The product owner has explicitly authorized more company-API capacity for the same Demo Run. The control plane needs a
bounded, durable way to represent that later authorization without changing what was originally admitted and without
asking the Agent to regenerate an already returned immutable result.

## Decision

1. Add a Demo-only, versioned command that appends a `run_limit_amendment` business fact. It requires the current
   amendment version, execution-control epoch, Task dispatch epoch, exact Matrix event ID, explicit reason, REST
   idempotency key, correlation ID, and authenticated actor.
2. The original Run Manifest remains byte-for-byte unchanged. Effective model-call, input-Token, and output-Token
   limits are the latest authorized amendment values, falling back to the frozen Manifest when no amendment exists.
3. Amendments are monotonic increases and remain bounded by implementation-level Demo ceilings. They cannot change
   USD, search, browser, timeout, remediation, model allowlist, scoring, Evidence, or report rules.
4. An amendment is legal only while the same Run and required Task are `NEEDS_ATTENTION` with failure class `BUDGET`,
   no external model invocation is active, and the exact Matrix result was already received for the current Task epoch.
5. The command returns the Run and Task to `RUNNING` without incrementing the dispatch epoch and without publishing a
   Task-ready event. It authorizes reprocessing only the named, byte-identical Matrix event.
6. Reprocessing preserves the original `matrix_event_receipt` and `matrix_handoff`. A separate append-only
   `run_limit_amendment_replay` fact binds one amendment to one payload hash and event ID. Repeated API calls or Matrix
   delivery remain idempotent.
   If an older listener first persisted a synthetic legacy failure for a generation-v4 Matrix event, the v4 adapter may
   bind the canonical event payload to that amendment only when the event ID, room sender, Run, Task and dispatch epoch
   match; the legacy receipt is `PROCESSED`, and its handoff is the exact empty-evidence, zero-confidence, high-risk
   contract-failure shape. The replay fact stores the canonical payload hash while the original synthetic receipt stays
   unchanged. No other payload mismatch is eligible.
7. Known usage from the accepted result is still persisted. The amendment raises the hard limit; it never deletes,
   discounts, rewrites, or relabels historical usage.
8. `SUBMISSION_UNKNOWN`, unknown result, missing Token counters, receipt reuse, or a different Matrix payload remains
   fail-closed and cannot use this command.
9. Published contracts remain immutable. The command uses a new additive OpenAPI contract generation.

## Consequences

### Positive

- The Demo preserves one formal Run and its complete recovery history.
- Original admission facts and later operator authorization remain separately auditable.
- An already returned Agent result can advance without another model call.
- Idempotency and epoch fencing prevent a quota amendment from becoming a generic replay bypass.

### Negative

- The control plane must read an effective-limit projection in addition to the frozen Manifest.
- Two append-only tables, a migration, a new command, and replay-specific tests are required.
- Operators must choose explicit new ceilings instead of relying on an unbounded override.

### Neutral

- Production Runs continue to use only their frozen Manifest limits.
- Report generation, scoring, Evidence auditing, exports, and public sharing are unchanged.

## Alternatives considered

### Edit the existing Run Manifest

Rejected because it would erase the original admission decision and invalidate its immutable identity.

### Delete or discount earlier failed-attempt usage

Rejected because the calls and Tokens were actually consumed and are business facts.

### Disable Token limits in `TOKEN_ONLY`

Rejected because ADR 0011 explicitly retains hard call and Token limits in that mode.

### Create a replacement Run with larger limits

Rejected because the Demo acceptance requires one formal Run and preserved project history.

### Re-dispatch the Agent after raising a process environment variable

Rejected because the complete Matrix result already exists and another model execution would add cost and drift.

## Failure and rollback

Disabling Demo mode makes the amendment command unavailable. Existing amendment and replay facts remain readable.
The original Manifest and all usage records remain unchanged. A failed reprocessing transaction does not consume the
replay authorization; a deterministic `BUDGET` result requires a new, higher amendment version.

## Verification gates

- migration upgrade/downgrade and tenant isolation pass;
- the original Manifest hash and JSON remain unchanged after amendment;
- stale version, non-monotonic limits, wrong event/epoch, active invocation, non-Budget failure, and production mode fail;
- one amendment permits one exact event replay without dispatch or model invocation;
- the same event with different bytes remains rejected;
- a generation-v4 canonical event may replace only its proven synthetic contract-failure projection;
- usage remains append-only and is admitted against the amended effective limit;
- current CreaTrades Run advances using its existing Matrix result, then completes normal browser acceptance.

## References

- `docs/adr/0001-frozen-boundaries-and-change-policy.md`
- `docs/adr/0011-token-only-provider-cost-accounting.md`
- `docs/adr/0019-demo-force-run-recovery.md`
- `docs/adr/0020-report-v22-baseline-citations-public-demo.md`
