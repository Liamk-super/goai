# ADR 0004: Agent-initiated NEEDS_INPUT clarification loop

- Status: Accepted for LaunchScope v0.3
- Date: 2026-08-08
- Scope: run-time clarification between a specialist Agent and the user
- Supersedes: none
- Amends: ADR 0002 (Agent output contracts only; the P0 Skill catalog is unchanged)

## Context

The v0.2 execution path gives a specialist Agent exactly three terminal
outcomes: `SUCCEEDED`, `BLOCKED`, `FAILED`. `BLOCKED` and `FAILED` are both
mapped to Task and Run `NEEDS_ATTENTION`, which ADR 0003 defines as a
fail-closed operator state that forbids automatic retry.

That leaves no representation for the ordinary business case in which a
specialist can complete its work but is missing one specific user-owned fact,
for example who pays, or which region is targeted. Today such a Run either
manufactures a hypothesis with `INSUFFICIENT_EVIDENCE`, or it is pushed into an
operator queue that the product user cannot resolve.

Reusing `NEEDS_ATTENTION` for this is wrong. It would blur an unrecoverable
fail-closed condition with a recoverable product question, and it would let a
user action clear a state whose runbook requires operator reconciliation.

The intake stage already has a durable gap-question mechanism
(`intake_gap_question`), but it runs before dispatch and cannot be triggered by
an Agent that is already executing.

## Decision

### 1. A new recoverable Task state

`TaskStatus.NEEDS_INPUT` is added. It is reachable only from `RUNNING`, and it
leaves only to `PENDING` (re-execution) or `CANCELLED`. It is distinct from
`NEEDS_ATTENTION`, which remains terminal and operator-owned.

A Task may enter `NEEDS_INPUT` only when at least one durable, unanswered
InformationRequest belongs to it. It may leave `NEEDS_INPUT` only when every
InformationRequest it owns is answered and the answers are durably committed.

### 2. A new recoverable Run edge

`RUNNING -> WAITING_FOR_USER -> RUNNING` is added to the Run transition table.
`WAITING_FOR_USER` already exists for intake; this ADR gives it a second
occurrence later in the lifecycle. The two are distinguished by
`evaluation_run.current_stage`, not by a new status:

- intake occurrence: `current_stage` is null or an intake stage
- clarification occurence: `current_stage` is an execution stage such as
  `DOMAIN_REVIEW`

Guards:

- `RUNNING -> WAITING_FOR_USER` requires at least one unanswered
  InformationRequest.
- `WAITING_FOR_USER -> RUNNING` requires that no unanswered InformationRequest
  remains and that an impact assessment has been recorded.

This edge carries no `failure_class`. A clarification is not a failure.

### 3. Agent output contract: minor additive version

`AgentHandoffV1` moves from `schema_version` `1.0` to the `1.x` family:

- `status` gains the value `NEEDS_INPUT`
- an optional `information_requests[]` array is added, each item carrying the
  target profile field, the question, why the Agent is blocked, and the
  affected dimension

Both changes are additive under the published consumer rule in
`packages/contracts/README.md`: a `1.0` consumer keeps reading the frozen
envelope and treats unknown payload additions as opaque. `information_requests`
is required to be non-empty when and only when `status` is `NEEDS_INPUT`, and it
must be empty for every other status.

### 4. Agent Identity contracts: Expand-Migrate-Contract

The six `packages/contracts/agents/*.v1.yaml` files are published and immutable.
They are not edited. Six new `*.v2.yaml` contracts are added with
`version: "2.0"`, declaring `information_request` as a permitted output. The
loader resolves a generation explicitly; v1 remains the default so that already
frozen RunManifest hashes keep verifying. `allowed_skills` and `allowed_tools`
are unchanged in v2, so this is not a Skill catalog change and ADR 0002 stands.

### 5. The user answer is written to Product Profile / Evidence, not to context

An answer is never injected into any Agent prompt or shared context directly.
The control plane:

1. validates the answer against the durable InformationRequest,
2. writes it into the structured `product_profile_draft.answered_fields` for the
   Run's product version, and records an append-only `information_request`
   answer row,
3. computes the affected Task set from the durable Task state,
4. returns only the affected Tasks from `NEEDS_INPUT` to `PENDING` and re-emits
   their task-ready events.

Tasks that already `SUCCEEDED` are not invalidated and not re-run. Agents read
the updated facts through the existing read-only MCP context tool, which is the
only channel by which durable Profile and Evidence reach an Agent.

### 6. First version is Agent-initiated only

Only the path "Agent asks -> user answers" is implemented. A user cannot inject
arbitrary unsolicited supplements during a Run, because that would invalidate
completed Agent results and force a broad re-run. Unsolicited supplement
handling is deliberately deferred to a later ADR.

### 7. No new event types

Per the deferral recorded with ADR 0005, clarification progress is projected
through the existing `run_status_history` rows and the durable SSE stream. No
event type is added to the frozen `evaluation-events.v1.json` union, and the
frozen contract test is untouched.

## Consequences

- A Run can pause on a real product question and resume without an operator,
  while `NEEDS_ATTENTION` keeps its strict fail-closed meaning.
- Re-execution is scoped to the parked Tasks rather than global, so paid model
  work already completed is not discarded.
- Two contract generations coexist for Agent identities. Callers must name the
  generation they want; frozen manifests continue to verify against v1.
- `WAITING_FOR_USER` becomes stage-dependent, so any consumer that infers
  intake purely from that status must also read `current_stage`.

## Verification

- Domain unit tests: the new Task and Run edges, the unanswered-request guard,
  the answered-plus-impact-assessed guard, and the rule that a clarification
  carries no failure class.
- Contract tests: the six v2 identities load, hash, and expose
  `information_request`; the six v1 identities still load and hash unchanged;
  `AgentHandoffV1` rejects `NEEDS_INPUT` without requests and rejects requests
  on any other status.
- Integration test: a specialist returns `NEEDS_INPUT`, the Run reaches
  `WAITING_FOR_USER`, the user answers, the answer is visible in the durable
  profile draft, and only the affected Task is dispatched again while an already
  succeeded sibling Task keeps its result.
