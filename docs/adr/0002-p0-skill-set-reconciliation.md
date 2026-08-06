# ADR 0002: Reconcile the P0 Skill set

- Status: Accepted for T1; promotion gate remains open
- Date: 2026-08-05
- Scope: LaunchScope V0.1 Skill catalog and UnifiedModel descriptors

## Context

The architecture baseline §15 freezes six initial P0 Skills:

1. `product-intake-normalizer`
2. `intake-gap-diagnosis`
3. `browser-product-audit`
4. `business-investment-assessment`
5. `evidence-grounding-audit`
6. `version-regression-verification`

The V2.0 reference proposal also uses `user-validation-designer` as an
additional Skill/P0 capability label. It additionally names
`geo-policy-trend-radar` and `market-evidence-research` in its proposed Skill
material. Those names are useful for reconciliation, but the reference is not
the architecture authority. The baseline's six P0 list and its strict Skill
engineering requirements therefore win for V0.1.

## Decision

1. V0.1 registers exactly the six baseline names as P0. No seventh P0 is added
   implicitly by the V2.0 proposal.
2. `user-validation-designer` is a reference candidate only. It is not
   executable, budgeted, allow-listed or assignable in the V0.1 P0 catalog.
   `geo-policy-trend-radar` and `market-evidence-research` are likewise
   reference capability labels until separately approved; their responsibilities
   do not alter the baseline Agent Identity set.
3. The UnifiedModel Skill contract makes this decision checkable: a Skill with
   `tier: P0` must have one of the six baseline `skill_ref` values. A proposed
   extra can only be represented as `tier: REFERENCE` and `lifecycle: PROPOSED`;
   this is semantic documentation, not registration or implementation.
4. This ADR does not select a Demo product, create a user-validation workflow,
   call a model/browser/search service, or add business behavior.

## Reconciliation table

| Capability name | Baseline §15 | V2.0 proposal | V0.1 treatment |
|---|---:|---:|---|
| `product-intake-normalizer` | P0 | core Skill | P0, registered |
| `intake-gap-diagnosis` | P0 | core Skill | P0, registered |
| `browser-product-audit` | P0 | core Skill | P0, registered |
| `business-investment-assessment` | P0 | core Skill | P0, registered |
| `evidence-grounding-audit` | P0 | core Skill | P0, registered |
| `version-regression-verification` | P0 | core Skill | P0, registered |
| `user-validation-designer` | absent | additional/P0 label | Reference candidate; not P0 |
| `geo-policy-trend-radar` | absent | proposed capability label | Reference candidate; not P0 |
| `market-evidence-research` | absent | proposed capability label | Reference candidate; not P0 |

## Promotion gate for an additional P0

Promotion of any reference candidate requires a new approved ADR (or an
explicit amendment to this decision) before registration. The proposal must
include, at minimum:

- an independently versioned input and output JSON Schema and Skill manifest;
- preconditions, allowed tools/domains/data, tenant and risk policy;
- failure classes, idempotency and `SUBMISSION_UNKNOWN` behavior;
- a bounded token/time/cost budget and RunManifest impact;
- unit, contract, security/prompt-injection and representative sample tests;
- evidence requirements and explicit treatment of simulated versus real user
  evidence;
- upgrade, deprecation and rollback instructions; and
- an owner and a compatibility plan for Agent Identity, API/event consumers and
  any persisted semantic descriptors.

Until that gate passes, a task cannot claim that the extra capability is a
baseline P0, and no implementation may hide it behind a temporary config or
an alias.

## Consequences

- The initial Skill catalog stays aligned with the only authoritative baseline.
- V2.0 ideas remain visible for later evaluation without silently expanding
  budget, permissions, test surface or Demo scope.
- The six P0 names can be used by later T6/T7 work with stable versioned
  descriptors; later promotion remains an explicit architectural choice.
