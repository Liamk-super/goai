# ADR 0008: Promote user-validation-designer dual-report presentation

- Status: Accepted
- Date: 2026-08-11
- Scope: User validation Skill 1.0.5, Presentation 0.4, additive contracts, and drain-before-cutover
- Amends: ADR 0007

## Context

ADR 0007 promoted `user-validation-designer@1.0.4` as an executable, checkpointed P0 Skill. The frozen V4
reference package adds deterministic summary and full report renderers, presentation examples, semantic prompt
calibration, and an execution gate that prevents experience claims without S4 evidence. LaunchScope must adopt
those improvements without replacing its runner protocol, exact knowledge registry, immutable artifact model, or
legacy replay path.

The following boundaries remain binding:

- `reference/`, released contracts, frozen contract tests, the `1.0.4` Skill Manifest, and migration `0017` are
  immutable.
- The complete machine-result JSON object is the sole canonical artifact. Human presentations are deterministic
  fields inside that object, not independent business objects.
- PostgreSQL remains the business-state source of truth. Matrix carries only the existing narrow, integrity-bound
  handoff and never transports a full report.
- Model submission, billing, and artifact-persistence uncertainty remain fail-closed and are never retried.

## Decision

1. Promote the integrated Skill as `user-validation-designer@1.0.5` with Presentation `0.4`. Keep the scoring
   methodology identifier `uvd-1.0.4` because the six-dimension scoring rules are unchanged.
2. Selectively merge the V4 presentation renderer, prompt calibration, experience-issue execution gate, examples,
   and tests into the project-owned package. Retain the existing runner, knowledge registry and knowledge bodies,
   package metadata, and LaunchScope adapter tests. Treat the V4 `knowledge.zip` only as the upstream package-hash
   source; do not copy it into the runtime package.
3. Keep input schema `launchscope://skills/user-validation-designer/0.1/input`. Add output schema
   `launchscope://skills/user-validation-designer/0.2/output` with nullable summary/full Markdown and HTML fields
   plus the legacy human-report aliases.
4. For admitted target users and a completed or partial result, require all four canonical presentation fields to
   be non-empty. Require `human_report == summary_report` and `human_report_html == summary_report_html`.
5. Persist the complete result and all presentations in one content-addressed JSON object. Store only Presentation
   version, availability, and content hashes in `skill_result.summary`. The artifact SHA-256 covers the complete
   object.
6. Add, rather than mutate, contract generations: Skill Manifest `1.0.5`, Run Manifest V3, User Validation OpenAPI
   V3, and migration `0018`. Keep Manifest `1.0.4`, Run Manifest V2, OpenAPI V2, migration `0017`, Agent contracts,
   MCP Tool V1, and Matrix handoff contracts unchanged.
7. Make the Skill registry generation explicit. `load_p0_v2()` selects UVD `1.0.4`; `load_p0_v3()` selects UVD
   `1.0.5`. New UVD-enabled Runs pin generation V3. When the feature is disabled, existing V2 behavior remains.
8. Expose presentations through an authorized, read-only report endpoint. The endpoint revalidates the object
   digest, database artifact digest, and selected content digest, returns `Cache-Control: no-store`, and rejects
   content over 1 MiB. Missing presentation fields return 404 rather than being synthesized from legacy results.
9. Show summary HTML by default in a scriptless sandbox iframe. Open the full report on a separate page. Offer
   summary/full HTML and Markdown downloads. Legacy, blocked, and presentation-free results retain the existing
   result summary and raw JSON link.
10. Cut over only after draining every `1.0.4` execution in `AWAITING_STEP` or `NEEDS_ATTENTION`. The rollout gate
    must fail closed while any such execution exists.

## Failure and recovery semantics

- Hash, schema, alias, required-presentation, object-size, or presentation-size mismatches fail closed. They do not
  invoke a model, regenerate a report, or replace an artifact.
- A final artifact persistence failure preserves the ADR 0007 `NEEDS_ATTENTION` behavior. No model or provider
  submission is retried.
- Report reads are side-effect free and tenant/workspace scoped. A missing field, corrupted artifact, cross-tenant
  reference, or content exceeding 1 MiB is not returned.
- Rollback first disables `LAUNCHSCOPE_USER_VALIDATION_ENABLED`. Runner code may be rolled back only after all
  `1.0.5` executions have left `AWAITING_STEP` and `NEEDS_ATTENTION`.
- Historical database rows, objects, Run Manifests, and both Skill Manifests remain readable and are never deleted
  or rewritten during rollback.

## Consequences

### Positive

- One immutable machine artifact deterministically supports concise reading, full review, download, and audit.
- Legacy Runs remain replayable while new Runs receive stronger experience-claim and presentation guarantees.
- Explicit catalog generations prevent an accidental duplicate-version load or silent version drift.

### Negative

- The API reads and verifies a larger artifact before serving any presentation.
- The Web UI gains a dedicated full-report route and authenticated download path.
- Cutover requires an operational drain query and can be delayed by executions that need attention.

### Neutral

- No new database table, column, model call, external submission lane, or Matrix payload is introduced.
- Recorded fixtures and local tests do not prove live AgentTeams, browser/search, paid-model, or production E2E.

## Alternatives considered

### Replace the existing package with the V4 folder

Rejected because it would discard the LaunchScope runner, knowledge registry, exact knowledge bodies, package
metadata, and adapter tests.

### Publish the V4 behavior under the existing 1.0.4 version

Rejected because executable behavior, prompts, output schema, and presentation guarantees change. Reusing the
version would violate immutable replay and hash locking.

### Persist each report as an independent object

Rejected because it would create multiple competing canonical artifacts and require new persistence and
reconciliation semantics. Deterministic fields inside the existing immutable object are sufficient.

### Run 1.0.4 and 1.0.5 concurrently for new work

Rejected for this cutover because it adds routing and operational ambiguity. Historical reads remain dual-version,
while new execution switches only after the old in-flight set is drained.

## Acceptance and rollout

- The V4 upstream 233-test suite and the six LaunchScope runner/knowledge adapter tests must remain green after
  integration; new dual-report and compatibility assertions are additive.
- Validate the package on Node 20.19 and the current Node 22 runtime where both runtimes are available.
- Validate new schemas, manifests, OpenAPI, migration, API integrity failures, tenant isolation, Viewer reads,
  Web sandboxing, downloads, and legacy fallback without modifying frozen contract tests.
- Roll out in this order: ADR, new Skill/contracts, migration, feature-disabled verification, recorded fixtures,
  PostgreSQL/object-store integration, local AgentTeams, and an explicitly authorized live case.

## References

- `docs/adr/0001-frozen-boundaries-and-change-policy.md`
- `docs/adr/0007-user-validation-designer-promotion.md`
- `docs/runbooks/user-validation-designer.md`
- `packages/contracts/run-manifest/run-manifest.v2.json`
- `packages/contracts/openapi/user-validation.v2.yaml`
