# ADR 0023: Append-only Demo canonical Matrix event recovery

- Status: Accepted
- Date: 2026-08-15
- Decision owners: LaunchScope product owner and evaluation control plane
- Amends: ADR 0019 Demo force recovery and ADR 0022 Demo Run limit amendments
- Does not change: PostgreSQL authority, immutable Agent reports, model-usage accounting, or external-submission fail-closed policy

## Context

The authorized CreaTrades Demo Run received and settled a complete generation-v4 Evidence Auditor result. The result is
still available under its original Matrix event ID and its report documents pass the current v4 contracts after the
deterministic legacy-metadata normalization defined by the report boundary. An older listener attempted the legacy
projection first, persisted a synthetic empty failure handoff, and a later canonical delivery was then masked as a
provider-receipt reuse error.

Re-dispatching the Task would call the model again even though the exact result and its usage are known. Editing the
existing receipt, handoff, Run, or report rows would erase the failure history. Creating another formal Run would also
violate the one-Run Demo acceptance boundary.

## Decision

1. Add a Demo-only command that appends a `run_canonical_event_recovery` fact for one Run, Task, dispatch epoch and
   Matrix event ID. The command requires the current control epoch, an explicit reason, REST idempotency key,
   correlation ID and authenticated actor.
2. Recovery is legal only when the Run and Task are `NEEDS_ATTENTION` with `SUBMISSION_UNKNOWN` caused by the proven
   receipt-reuse projection; the current Task epoch has no active or uncertain model invocation; both known model Token
   usage and call count are persisted; and the named receipt is already `PROCESSED`.
3. The existing handoff must have the exact synthetic legacy-failure shape: high risk, zero confidence, approval
   required and no Evidence. The original receipt and handoff remain unchanged.
4. The command returns the same Run and Task to `RUNNING` without changing either epoch, publishing a Task-ready event,
   or invoking an Agent. It authorizes only the byte-identical canonical event bound to the existing receipt.
5. Successful replay appends a `run_canonical_event_replay` fact. One recovery permits one event replay; subsequent
   deliveries use the ordinary duplicate projection.
6. The canonical v4 event still passes all report, Evidence, scoring and language validation. Recovery does not relax
   a contract or allow an operator to edit Agent output.
7. Production mode and any missing, active, uncertain, mismatched, unaccounted or differently shaped result remain
   fail-closed.
8. Published contracts remain immutable. The command has a new additive OpenAPI contract generation.

## Consequences

The one formal Demo Run can continue from an already known external result without another model call, while preserving
both the original synthetic failure and the later recovery as append-only facts. The cost is one migration, a narrowly
scoped command, replay authorization logic and additional tests.

## Alternatives considered

- Re-dispatch the Evidence Auditor: rejected because it would repeat a completed model execution.
- Rewrite or delete the synthetic receipt and handoff: rejected because PostgreSQL history must remain immutable.
- Treat any receipt-reuse error as replayable: rejected because it would weaken event identity and submission safety.
- Create a replacement formal Run: rejected because the Demo requires one persisted Run.

## Verification gates

- migration upgrade/downgrade and tenant isolation pass;
- production mode, stale epochs, wrong event, active or uncertain invocation, missing usage and a non-synthetic handoff fail;
- recovery changes neither dispatch nor control epoch and publishes no dispatch event;
- one exact canonical event advances and appends one replay fact without a model invocation;
- the current CreaTrades Run advances from its existing Evidence Auditor result into normal supervisor synthesis.

## References

- `docs/adr/0017-delivery-scoped-model-ledger.md`
- `docs/adr/0019-demo-force-run-recovery.md`
- `docs/adr/0020-report-v22-baseline-citations-public-demo.md`
- `docs/adr/0022-demo-run-limit-amendments.md`
