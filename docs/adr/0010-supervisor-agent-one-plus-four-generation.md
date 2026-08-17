# ADR 0010: Supervisor Agent physical 1+4 generation

- Status: Accepted
- Date: 2026-08-11
- Decision owners: LaunchScope architecture and evaluation control plane
- Supersedes: ADR 0009 physical 1+5 topology and generation-v4 rollout decision
- Amends: ADR 0003 AgentTeams bundle cardinality for the new generation only

## Context

ADR 0009 established `evaluation-manager` as the AgentTeams business Team Leader but retained a physical 1+5
topology: the manager, four parallel domain Workers, and an independent evidence auditor. The approved design in
`docs/design/主管Agent_1+4架构设计_V1.md` replaces that topology with one supervisor and four Workers. Geography,
policy, timing, platform, and freshness responsibilities move into the user, product, and investment domains and
remain independently checked by the auditor. There is no hidden `geo-policy-trend` Worker in the new generation.

The current workspace also contains concurrent, uncommitted work in the dispatch, handoff, intake, persistence,
domain-state, AgentTeams-resource, packaging, and Web paths. One tracked file and several untracked files under
`reference/` are already user-modified. This decision does not claim ownership of those edits. Implementation must
extend the current working copy surgically, preserve all existing changes, and leave `reference/`, released
contracts, and `packages/contracts/tests/` unchanged.

## Decision

### 1. Physical topology and role boundaries

The new product topology is exactly five business Workers:

1. `evaluation-manager` as the sole `team_leader`;
2. `user-evidence` as a domain Worker;
3. `product-engineering` as a domain Worker;
4. `business-investment` as a domain Worker; and
5. `evidence-auditor` as the serial independent audit Worker.

The AgentTeams global Manager, Intake Model, deterministic control plane, scoring engine, and report renderer are
platform components and do not count toward the physical 1+4. `geo-policy-trend` remains a readable historical
identity but cannot be materialized for a new-generation Run.

The supervisor may produce only a versioned plan, one controlled replan proposal, and a versioned synthesis. It
cannot perform domain research, call domain tools, directly create or mutate Tasks, expand permissions or budget,
write business state, score or rescore Findings, rewrite audit results, change the deterministic recommendation,
or commit a report. A disagreement with the deterministic recommendation is persisted as `decision_conflict`.

### 2. Deterministic execution order

The control plane enforces this order:

```text
RequirementBriefV1
-> supervisor PLAN
-> plan validation and Task materialization
-> user/product/investment first round in parallel and mutually isolated
-> evidence audit after all planned domains reach a legal terminal state
-> at most one targeted remediation round
-> at most one re-audit round
-> deterministic scoring
-> supervisor SYNTHESIZE
-> reference validation and backend rendering
-> Decision, Report, and Project Dossier commit
-> COMPLETED
```

The auditor never edits a source Finding. Missing or failed domains remain explicit coverage gaps. A required
domain failure blocks the corresponding focused conclusion. A known failure without uncertain external effects
may permit a partial report when the selected score profile allows it.

### 3. Intake and user interaction boundary

Intake is a separate, non-agent model service. It produces `RequirementBriefV1` without tools, scheduling,
scoring, reporting, business writes, or model memory. Raw input and every normalized revision are durable.
High-confidence input with no critical ambiguity proceeds without confirmation. Critical ambiguity, a model-added
assumption, material scope/cost change, or new external permission uses the single supervisor conversation to ask
only the required questions. Runtime changes may alter only Tasks that have not started; completed results remain
immutable and belong to a new plan/report version.

### 4. Expand-Migrate-Contract generation

Implementation adds, rather than modifies, the following artifacts:

- `RequirementBriefV1`, `RequirementChangeV1`, `ManagerPlanV1`, `AgentTaskTicketV3`, `AgentHandoffV3`,
  `AuditRequestV3`, `AuditResultV3`, `ScoreProfileV1`, and `ManagerSynthesisV1` contracts;
- five Agent Identity v4 contracts;
- Run Manifest v4 with an explicit physical-topology declaration and hashes for every identity, contract, score
  profile, Skill, prompt, knowledge package, and allowed tool;
- generation-v4 persistence and an AgentTeams resource bundle containing exactly the five Workers above.

Released V1-V3 identities, Handoff/Audit schemas, Run Manifests, reports, migrations, and frozen contract tests are
not edited. Historical 1+5 Runs resolve their frozen manifest and retain `GEO_POLICY_TREND` Findings and reports.
New readers branch on manifest generation instead of inferring topology from current configuration.

### 5. Feature flag, admission, rollback, and concurrency

`LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED` gates admission of new generation-v4 Runs and defaults to `false`. When false,
the existing static 1+5 path is unchanged. When true, only newly admitted eligible Runs use Run Manifest v4 and
the five-Worker bundle. A Run never changes generation in place.

Rollback first disables new v4 admission. In-flight v4 Runs continue on their frozen manifest or remain
fail-closed for operator reconciliation; they are never converted to the legacy topology. Historical v4 plans,
Findings, audits, decisions, reports, dossier revisions, and object bodies remain readable. No rollback deletes or
rewrites data. Activating or deactivating Worker packages requires draining incompatible in-flight deliveries.

### 6. Business truth, artifacts, and collaboration

PostgreSQL is the sole authority for Requirement Briefs, plans, Tasks, delivery receipts, Findings, audits,
scores, decisions, report metadata, and dossier versions. Domain reports, evidence bodies, audit bodies, and final
report bodies are immutable object-store objects bound by SHA-256. Matrix and AgentTeams carry bounded Task
tickets, collaboration projections, delivery acknowledgements, and object references only; messages and shared
files cannot directly change business state.

### 7. Failure policy

`SUBMISSION_UNKNOWN`, unknown usage or billing, paid-call timeout, unknown object persistence, and any uncertain
external side effect transition the Run to `NEEDS_ATTENTION`. They prohibit automatic retry, failover, replacement
submission, replan, role substitution, or manual/raw-SQL settlement. Known local validation failures may receive
one correction only when the control plane proves no external side effect occurred. Targeted remediation and
re-audit are each capped at one durable round.

## Compatibility window

Legacy and generation-v4 producers and readers coexist indefinitely for historical access. The default-off flag
is the migration window. Generation-v4 becomes eligible for broader rollout only after new contract tests, domain
and control-plane unit tests, PostgreSQL migration/integration tests, bundle validation, and all minimum scenarios
in the approved design pass. Removal of any legacy identity, contract, field, reader, or report path requires a
future ADR and a separately authorized Contract phase.

## Security and tenant impact

Every Worker receives only a tenant-, Run-, Task-, agent-, tool-, and expiry-scoped capability. The supervisor has
no domain MCP endpoints. External targets, budgets, deadlines, and tool quotas are frozen in the Run Manifest.
Secrets, raw credentials, private report bodies, and evidence bodies cannot enter Matrix, Agent prompts, or
reports. All new tables use the existing tenant composite-key and RLS conventions.

## Consequences

### Positive

- The physical topology matches the approved product model without weakening independent evidence calibration.
- Time and geography become cross-domain concerns instead of a disconnected parallel verdict.
- Planning, audit, scoring, synthesis, and completion become independently testable deterministic gates.
- Legacy Runs and historical geography data remain readable without a data rewrite.

### Negative

- Two physical topologies and multiple contract generations must remain supported.
- The control plane gains additional persistence, validation, and report-commit stages.
- Operational cutover requires generation-aware Worker packaging and drain checks.

### Neutral

- Local contract, unit, PostgreSQL integration, and recorded-flow evidence do not prove real AgentTeams, real
  model, real tool, or paid external end-to-end execution.
- M7 UI work and authorized external acceptance are outside this decision's M0-M6 implementation goal.

## Alternatives considered

### Edit ADR 0009 and released contracts in place

Rejected because it would erase the historical 1+5 decision and break immutable replay.

### Keep a hidden geography Worker

Rejected because it violates the approved physical 1+4 topology and would split time/region responsibility from
the three accountable domains.

### Let the supervisor materialize Tasks or determine scores

Rejected because an Agent proposal cannot become a second business-state or scoring authority.

### Treat Matrix collaboration as workflow state

Rejected because Matrix delivery and messages are projections and cannot provide transactional business truth.

## Verification and promotion gates

- Byte/hash checks prove every pre-existing contract and frozen contract-test source is unchanged.
- New contracts include positive, negative, hash-lock, and cross-reference tests.
- Identity and bundle tests prove five v4 identities, exactly five Workers, one leader, no geography Worker, and
  disabled peer scheduling.
- PostgreSQL migration tests prove additive upgrade, RLS, legacy reads, immutable versions, and full commit gates.
- Control-plane tests cover all twelve minimum scenarios in design section 18.
- Rollback tests prove the default-off legacy path and generation-pinned in-flight behavior.

## References

- `docs/design/主管Agent_1+4架构设计_V1.md`
- `docs/adr/0001-frozen-boundaries-and-change-policy.md`
- `docs/adr/0003-demo-agentteams-and-async-boundaries.md`
- `docs/adr/0004-needs-input-clarification-loop.md`
- `docs/adr/0006-agent-runtime-context-accounting-and-deadlines.md`
- `docs/adr/0009-agentteams-native-business-team-leader.md`
