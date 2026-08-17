# ADR 0007: Promote user-validation-designer as an executable P0 Skill

- Status: Accepted
- Date: 2026-08-11
- Scope: User Agent validation runtime, evidence calibration handoff, and compatible contract generations
- Amends: ADR 0002

## Context

ADR 0002 deliberately kept `user-validation-designer` outside the executable P0 catalog until its schemas,
permissions, budgets, failure behavior, evidence treatment, tests, and compatibility plan were approved. The
reference package now provides a frozen V1.0.4 state machine, schemas, examples, deterministic scoring rules,
and adversarial tests. The folder named by the product owner contains the contract material but no executable
`src/`; the sibling `用户共创skill.zip` contains the missing source and tests. Every overlapping file has the
same SHA-256 digest.

Passing only the Skill prompt to AgentTeams would not enforce its gates, evidence ceilings, retries, state
integrity, or report handoff. LaunchScope also needs durable execution state and a bounded interface through
which the Evidence Auditor can calibrate the result without transferring a full report through Matrix.

The following invariants remain binding:

- PostgreSQL is the only business-state source of truth; object storage holds immutable bodies addressed by hash.
- AgentTeams and Matrix transport work and references but cannot directly change business state.
- Reference inputs, released V1 contracts, and frozen contract tests remain unchanged.
- Unknown model submission, billing, or artifact persistence is fail-closed and is never automatically retried.
- The current 1+5 topology and the separate geography task remain active in this change.

## Decision

1. Promote `user-validation-designer@1.0.4` as an executable P0 Skill behind a dedicated feature flag. The
   reference folder remains read-only. A project-owned Node workspace package carries the verified source,
   tests, prompts, schemas, examples, and an upstream digest inventory.
2. Execute the deterministic Skill engine as a stateless JSON stdin/stdout runner. It has no database, object
   store, network, model, or secret access. The API control plane owns authorization, idempotency, checkpoints,
   persistence, and recovery.
3. The existing User Agent performs the model work for the Skill steps. The runner supplies the bounded prompt,
   exact knowledge identifiers, and output schema for each step, then validates and advances state. LaunchScope
   does not add a second model/provider submission lane.
4. Store private step bodies and final reports as content-addressed objects. Store execution status, revisions,
   hashes, evidence relations, and audit decisions in PostgreSQL. Matrix handoffs carry only a narrow summary,
   `skill_result_ref`, and `skill_result_sha256`.
5. Add new contract generations rather than changing released V1 artifacts: Skill Manifest V2, Run Manifest V2,
   Agent Identity generation V3, AgentHandoff V2, AuditResult V2, and Evidence Card V2. Legacy generations remain
   loadable and replayable.
6. UVD-issued evidence is always simulated and is capped at E2. Caller-supplied real evidence is ingested
   separately, preserves E3-E5 only after integrity and applicability checks, and always wins over conflicting
   simulation without averaging. The Evidence Auditor records the governing `KB-EVD-*` rule identifiers.
7. Support `first_validation`, automatically selected compatible `version_regression`, and explicit
   `evidence_recheck`. A recheck creates an append-only child Run linked to its baseline; it never mutates the
   completed Run or report.
8. External user contact, recruitment, messaging, publishing, PII collection, and spending remain design-only
   actions that require a human outside this Skill runtime.
9. Bound the User Worker to 32 AgentTeams iterations and a 1,200-second task deadline. Other workers retain their
   existing limits. The selected model, Skill, knowledge, prompt, schema, and validation-script hashes are frozen
   in the Run Manifest.
10. A later combined business/geography Skill and one-score contract require a separate ADR. This promotion does
    not activate a 1+4 topology or change the four-dimension synthesis rule.

## Failure and recovery semantics

- A repeated idempotency key with the same request hash replays the committed response; a different hash returns
  `IDEMPOTENCY_CONFLICT`.
- Invalid input, PII, missing capabilities, schema failures, and checkpoint mismatches do not invoke a model or
  fabricate an answer. A step may be retried at most twice for a known validation failure.
- `SUBMISSION_UNKNOWN`, unknown billing, and unknown object persistence set the Run to `NEEDS_ATTENTION`; they do
  not trigger retry, failover, provider switching, or replacement submission.
- A process restart resumes from the last committed revision and never repeats a completed step.
- Content-addressed objects are written before the PostgreSQL transaction that publishes their references. A
  failed transaction may leave an unreferenced object for retention cleanup but cannot publish a partial result.

## Consequences

### Positive

- The User Agent genuinely executes the approved Skill instead of receiving a name-only prompt alias.
- Deterministic policy remains outside model discretion while model reasoning stays in the existing AgentTeams lane.
- Evidence calibration is traceable to immutable artifacts, evidence identifiers, and knowledge-rule identifiers.
- Legacy Runs and clients remain compatible.

### Negative

- The API runtime must bundle Node.js and maintain a small cross-language runner protocol.
- Multi-step execution adds checkpoints, object writes, and a larger User Worker iteration budget.
- A new contract generation and additive database migration increase the validation surface.

### Neutral

- Full live acceptance still requires an explicitly authorized case; recorded fixtures cannot prove external E2E.
- The current 1+5 team remains until a separately governed topology change is ready.

## Alternatives considered

### Give the Skill directory directly to the User Agent

Rejected because prompts cannot enforce state integrity, evidence ceilings, idempotency, persistence, or audit
handoff, and the named directory does not contain executable source.

### Let the Skill runner call a model provider directly

Rejected because it creates an additional submission, billing, retry, and reconciliation lane outside the frozen
AgentTeams Run policy.

### Port the deterministic engine to Python

Rejected for this promotion because it would duplicate a frozen, heavily tested V1.0.4 implementation and create
semantic drift. A stateless Node runner has lower compatibility risk.

### Send the complete report through Matrix

Rejected because the report is large and private. Matrix carries only an integrity-bound reference; the auditor
uses a tenant- and task-scoped read capability.

## Acceptance and rollback

- The imported upstream suite must retain at least the planned 203/203 passing baseline after the canonical Evidence
  Card dependency is supplied. The admitted V1.0.4 archive currently contains 221 upstream tests; the project runner
  and exact knowledge-package checks add six more, so the current executable gate is 227/227.
- New runtime, contract, persistence, security, AgentTeams packaging, and UI flow tests must pass while all legacy
  V1 tests remain unchanged and green.
- The feature ships disabled, then progresses through recorded fixtures, local PostgreSQL/object-store integration,
  local AgentTeams execution, and an authorized live case.
- Rollback disables the new Skill and V3 identities for new Runs. Existing immutable results and audits remain
  readable; no data is deleted or rewritten.

## References

- `docs/adr/0001-frozen-boundaries-and-change-policy.md`
- `docs/adr/0002-p0-skill-set-reconciliation.md`
- `docs/adr/0006-agent-runtime-context-accounting-and-deadlines.md`
- `reference/skills/user-validation-designer/SKILL.md`
- `reference/agent知识库/用户共创Agent知识库与行为决策逻辑_副本.md`
- `reference/agent知识库/证据校准Agent知识库与决策逻辑_副本.md`
