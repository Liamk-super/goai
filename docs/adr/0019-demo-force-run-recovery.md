# ADR 0019: Demo force recovery for attention Runs

- Status: Accepted
- Date: 2026-08-13
- Scope: Demo Run execution recovery
- Supersedes: none

## Context

A Demo Run can stop in `NEEDS_ATTENTION` after a timeout, unavailable runtime, or unknown submission. The existing
`/resume` command only resumes a safely paused execution-control checkpoint, so these Runs remain visible but cannot
continue from the browser. Demo operators need to continue the same Run without duplicating the project or discarding
completed work.

## Decision

1. Add a Demo-only `POST /api/v1/runs/{run_id}/recover` command. It requires an explicit `force` value and the current
   execution-control epoch.
2. The command keeps the Run identity and completed Tasks, resets executable unfinished Tasks to `READY`, increments
   their dispatch epochs, advances the control epoch, and uses the existing task-ready Outbox path.
3. Demo force recovery may override `TIMEOUT`, `RUNTIME_UNAVAILABLE`, and `SUBMISSION_UNKNOWN`. The ordinary Run and
   Task transition paths remain fail-closed.
4. Old deliveries, invocations, usage, and evidence remain append-only. Delivery acknowledgement, timeout scanning,
   and late handoffs are fenced by the current dispatch epoch.
5. Reuse the existing REST idempotency and correlation headers. Do not add recovery-specific credentials, database
   tables, migrations, approval flows, or content fingerprints.
6. Persist recovery status history and reuse the constrained `run.resumed` execution event with a
   `run.force_recovered` reason marker. This preserves distinct audit semantics without a schema migration.
7. Store the idempotency record in the existing `RESUME` operation slot; the request hash still includes
   `RECOVER` and `FORCE`, and the stored response retains the recovery-specific shape.

## Consequences

The Demo can continue an interrupted project from the saved state with one user confirmation. Completed work remains
stable and a repeated command does not create another attempt. The explicit override can duplicate an external action
whose old result arrives late, so it is disabled outside Demo mode and is not a production reconciliation mechanism.

## Alternatives considered

- Create a replacement Run: rejected because it breaks the user's expectation that the paused project continues.
- Reuse `/resume`: rejected because safe pause and force recovery have different state and product semantics.
- Require reconciliation before every recovery: rejected for this Demo-specific workflow because it leaves the
  operator without a practical continuation path.

## Failure and rollback

Disabling `LAUNCHSCOPE_DEMO_MODE` removes the recovery endpoint behavior. Existing recovery events and prior attempts
remain readable. No schema rollback is required.
