# ADR 0024: Report v3 quality contract and stage-aware frontend admission

- Status: Accepted
- Date: 2026-08-15
- Decision owners: LaunchScope product, report, and evaluation control plane
- Amends: ADR 0020 report v2.2 baselines, citations, and public Demo evidence
- Does not change: physical 1+4 topology, deterministic scoring, PostgreSQL authority, audit authority, or fail-closed policy

## Context

The published `SupervisorReportDocumentV2` and `SpecialistReportDocumentV2` established one immutable report body,
Claim-level Citations, confidence, source directories, and summary/full projection. The 2026-08-15 quality audit found
that those contracts cannot safely carry the complete accepted product experience:

- the supervisor body does not freeze locale, four deterministic dimension values, or cited positive and negative
  score-driver Claim identities;
- Evidence coverage can still be presented as generic completeness without a definition version;
- the specialist body accepts an arbitrary minimally populated `domain_payload`, so a document may validate without a
  useful role-specific professional structure;
- the report body does not freeze the three user-facing P0/P1/P2 issues selected for the concise first screen;
- the frontend admission flow does not distinguish idea, prototype, Demo/MVP, and real-user stages before a formal Run.

The v2 contracts and their hash locks are published artifacts. Editing them in place would break immutable replay and
historical readers. Adding display-only data from mutable tables would create a second report truth and is also rejected.

## Decision

### 1. Add a report-v3 contract generation

Add `SupervisorReportDocumentV3`, `SpecialistReportDocumentV3`, and `ManagerSynthesisV3` as new versioned contracts.
Keep all v1/v2 contracts, artifacts, hashes, routes, and readers unchanged.

The v3 supervisor canonical document freezes:

- `locale`;
- the four deterministic dimensions `user_value`, `product_capability`, `investment_potential`, and
  `evidence_quality`;
- each dimension value or an explicit unavailable value, evidence level, and positive/negative/pending Claim IDs;
- an Evidence-coverage definition version and separate quality/independent-support explanation;
- no more than three P0/P1/P2 issue projections, each bound to an existing Claim;
- the existing authoritative index, recommendation, comparison, confidence, actions, Citations, source directory, and
  four specialist cards.

The control plane derives dimensions and driver candidates from audited Findings and the deterministic score result.
The Supervisor may organize prose and actions but cannot author or alter scores, driver polarity, audit strength,
coverage, confidence, or recommendation.

### 2. Require role-specific specialist structures

`SpecialistReportDocumentV3` uses a discriminated `domain_payload`:

- user evidence: target segments, jobs/scenarios, behavioral Evidence, retention/payment, validation plan;
- product engineering: stage gate, core flows, delivery/reliability, dependencies/security, retest gates;
- business investment: business model, unit economics, competition/market, investment gates, compliance scope;
- Evidence audit: coverage by dimension, source independence, conflicts, calibration decisions, Evidence gaps.

Every specialist document also freezes locale, risks/gaps, actions, cited sources, and a human-readable audit summary.
Unsupported positive content is not required; an explicit Evidence gap satisfies the structure without inventing facts.

### 3. Preserve monotonic Claim strength and source relevance

A Claim cannot be stronger than any Citation set admitted to support it. A `VERIFIED` Claim requires at least one
`VERIFIED` supporting Citation. A `DOWNGRADED` Claim may use `VERIFIED` or `DOWNGRADED` supporting Citations.
`PENDING_VALIDATION` and conflicted Claims are never score-bearing. The user-visible source directory contains only
sources referenced by admitted support, counter-Evidence, or necessary background Citations and is deduplicated by
canonical identity.

### 4. Use one immutable body across every projection

Workspace Web, no-login public Demo, summary/full specialist views, print/PDF, and ZIP must load the same committed v3
object and verified SHA. Projection metadata may add access URLs, but it cannot add or replace report facts. Export cache
keys continue to include canonical SHA, renderer version, locale, view, and Evidence-inclusion choice.

### 5. Add stage-aware frontend admission without changing backend truth

The homepage starts with a product description, a stage choice, and nearby material/link entry. The client may create a
durable project record only when the user starts this flow; that record name is derived as an editable presentation
default, not treated as business judgment.

Stage routing is presentation plus control-plane admission:

- idea: product-definition conversation only; formal full evaluation remains unavailable;
- prototype: lightweight review while target user and validation objective are confirmed;
- Demo/MVP and real-user/live stages: eligible for the existing formal flow after project-portrait confirmation.

Frontend labels map to persisted fields and internal identities. They do not rename Skills, Agent identities, enums, or
tasks. Conversation displays one main question at a time, at most two quick choices, and saves confirmed rounds in the
existing project-scoped draft.

## Compatibility and migration

- Existing report v1/v2 objects and public/private routes remain readable and byte-identical.
- New v3 readers branch only on explicit schema version; they never infer a generation from current configuration.
- New v3 production is admitted behind a new frozen Run/report profile. An in-flight Run never changes report generation.
- Rollback disables new v3 admission. It does not delete or rewrite v3 or earlier artifacts.
- No database rewrite is required for historical reports; new metadata remains inside the immutable canonical object and
  existing append-only report metadata.

## Security and failure behavior

- PostgreSQL continues to authorize tenant, Run, report, public token, Evidence, and export relationships.
- Public routes retain run-scoped token checks, no-login disclosure, `noindex,nofollow`, and safe Evidence handling.
- Missing/corrupt canonical objects, Citation incompatibility, locale mismatch, sparse specialist content, or export
  ambiguity fail closed. None triggers a model rerun, replacement submission, or manual JSON fallback.
- `SUBMISSION_UNKNOWN`, unknown usage/billing, and uncertain external side effects remain `NEEDS_ATTENTION` without
  automatic retry.

## Consequences

### Positive

- Four-dimensional score explanations and issue priority are replayable report facts rather than frontend guesses.
- Specialist full views have enforceable professional depth while remaining honest about Evidence gaps.
- Chinese locale and Claim/Citation identities stay stable across Web, public Demo, PDF, and ZIP.
- Historical report objects and hashes remain untouched.

### Negative

- v2 and v3 report producers/readers must coexist.
- Agent package and renderer conformance tests expand for role-specific payloads.
- New v3 fixtures are required for Recorded browser acceptance.

### Neutral

- The deterministic score weights and four recommendation enums do not change.
- L1-L4 local/Recorded verification is not Live AgentTeams/model/search or paid-provider closure.

## Alternatives considered

### Edit report v2 files in place

Rejected because their hashes are frozen and historical replay must remain stable.

### Attach dimension values from a mutable API endpoint

Rejected because Web/PDF/ZIP could drift from the committed canonical document.

### Keep arbitrary specialist payloads and validate only in React

Rejected because incomplete Agent artifacts would remain valid business inputs and other projections could omit the same
content.

### Enter every stage into a formal full evaluation

Rejected because idea-only products cannot support the evidence claims implied by a full prediction.

## Verification gates

- v2 contract hashes and legacy readers remain unchanged;
- v3 contract tests reject missing locale/dimensions, incompatible Claim/Citation strength, sparse specialist payloads,
  and more than three concise issues;
- one canonical SHA supplies workspace, public, summary/full, PDF, and ZIP projections;
- stage-aware homepage, conversational supplementation, project portrait, stable run page, project archive, report
  hierarchy, keyboard/mobile/reduced-motion, and desktop/narrow Chromium checks pass;
- Recorded evidence is reported separately from any later authorized Live evidence.

## References

- `docs/adr/0001-frozen-boundaries-and-change-policy.md`
- `docs/adr/0010-supervisor-agent-one-plus-four-generation.md`
- `docs/adr/0012-supervisor-one-plus-four-only-admission-and-agent-reports.md`
- `docs/adr/0020-report-v22-baseline-citations-public-demo.md`
- `docs/plans/2026-08-15-hit-predictor-frontend-v1-goal-brief.md`
- `docs/plans/2026-08-15-launchscope-v22-report-quality-remediation-goal.md`
