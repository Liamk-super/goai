# ADR 0001: Frozen boundaries and change policy

- Status: Accepted for T1
- Date: 2026-08-05
- Scope: LaunchScope V0.1 contract package
- Supersedes: none

## Context

T1 must turn the LaunchScope implementation plan into checkable boundaries
before any application, worker or infrastructure code is added. The document
`docs/势能引擎技术架构基线_V1.0.md` freezes the domain and architecture:
PostgreSQL is the business-state source of truth; Matrix is collaboration;
RocketMQ carries reliable commands/events; REST and SSE are the control-plane
interfaces; UnifiedModel is an exchange semantics layer; and Skills and Agent
Identities are versioned, permissioned objects.

The repository also contains a plan and read-only reference materials. They
are useful context, but they must not silently widen the baseline or select a
competition Demo product.

## Decisions

### 1. Authority and T1 boundary

1. The baseline is the only architecture fact source. A plan can sequence work,
   and a reference can identify a proposal, but neither can override a frozen
   baseline decision.
2. T1 is limited to JSON Schema, OpenAPI, the contracts README, these two ADRs
   and their local contract tests. It does not create `apps/`, domain services,
   database migrations, workers, adapters, Compose files or business flows.
3. `docs/势能引擎技术架构基线_V1.0.md` and every file under `reference/` are
   read-only inputs. Changing either requires a separately authorized decision,
   not a T1 implementation edit.
4. No Demo product is chosen or assumed by a contract example. Examples use
   neutral identifiers and labels only.

### 2. Message boundary

Events and commands use the shared envelope in
`packages/contracts/events/envelope.schema.json`. The required cross-cutting
fields are `event_id`, `tenant_id`, `run_id`, `task_id`, `correlation_id`,
`causation_id`, `idempotency_key`, `schema_version`, `occurred_at` and
`payload`. Events add `event_type`; commands add `command_type`. The frozen
`event_id` field name is retained for commands as the transport message
identity so a consumer cannot accidentally create a second identity field.

The command is a request, not a state authority. The control plane validates
and commits state, approval, budget, idempotency and audit records in the
PostgreSQL boundary, then publishes an outbox event. Matrix, RocketMQ
consumers, Agents and Workers cannot directly update business state.

### 3. API and recovery boundary

REST writes require `Idempotency-Key` and `X-Correlation-Id`. A repeated key
with the same request hash replays the original accepted result; a different
hash returns `IDEMPOTENCY_CONFLICT`. Responses expose a uniform error code,
correlation id and retryability. List operations use opaque cursor pagination.

SSE is a projection of durable state and events, not an in-memory progress
channel. Each frame has an opaque durable cursor in `id`. On reconnect,
`Last-Event-ID` wins over the query `cursor`; the server replays strictly after
the cursor. No cursor starts with a durable snapshot. An invalid or expired
cursor returns `CURSOR_INVALID`, requiring a state refetch. This gives a
defined recovery path without claiming high availability.

### 4. UnifiedModel boundary

UnifiedModel v1 maps Product, ProductVersion, EvaluationRun, AgentIdentity,
Task, Skill, Tool, Hypothesis, Finding, Evidence and Decision plus relations.
Its schema carries a non-authoritative status projection and an explicit
`transaction_authority.system: postgresql` declaration. It does not perform
state transitions, budget deduction, approval resolution, idempotency or audit
writes.

### 5. Independent versions and compatibility

API, event, command, Skill, Prompt, Agent Identity, rule, UnifiedModel and Run
Manifest versions are independent. A published contract file is immutable.

| Change | Required action |
|---|---|
| Breaking field/type/removal or incompatible semantics | New major contract file and ADR; support a migration window |
| Additive optional field or consumer-tolerated enum extension | New minor version after compatibility test |
| Description/example/non-semantic correction | Patch release; do not alter required semantics |

Consumers must accept the current and immediately previous released minor event
version. The repository has no earlier published LaunchScope event artifact,
so the T1 compatibility test verifies the frozen envelope and the behavior of
an older tolerant consumer; it does not claim an already deployed version.

Use Expand-Migrate-Contract: add a new compatible shape, migrate producers and
consumers with observable evidence, then remove the old shape only in a later
major version. Never edit a released schema in place.

### 6. Fail-closed boundary

`SUBMISSION_UNKNOWN`, unknown billing or an uncertain external side effect is
not an automatic retry. The contract exposes a non-retryable error and a
`run.needs_attention` path. Reconciliation, model/tool switching, resubmission,
manual settlement and raw-SQL status edits are outside T1 and remain forbidden
until a separately versioned, CAS-controlled design exists.

## Consequences

- T2 and later work can consume stable filenames and field names without
  inventing a parallel envelope or API error format.
- The contract package is intentionally not a runnable control plane; schema
  tests prove shape and compatibility rules only.
- A future implementation must add concrete persistence and outbox/inbox
  evidence while preserving these boundaries.
- A reference proposal can be represented as a non-executable descriptor, but
  it cannot become a P0 Skill or a Demo assumption through examples.

## Change process

Any request that changes a frozen boundary must stop at an ADR before editing a
contract. The ADR must state the baseline conflict or gap, alternatives,
security and tenant impact, compatibility window, migration, rollback and
tests. Existing dirty files are preserved; T1 does not authorize Git staging,
commits, pushes, deployment or external calls.
