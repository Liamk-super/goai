---
name: evidence-grounding-audit
description: Audit Product, User, and Investment Agent claims against traceable evidence; calibrate claim strength, deduplicate shared facts and sources, distinguish conflicts from decision tensions, and emit a bounded SupervisorHandoff. Use when multi-agent conclusions must be accepted, downgraded, rejected, or routed to evidence gaps without redoing domain analysis.
---

# Evidence Grounding Audit

Audit whether another Agent's claim is supported strongly enough to enter supervisor judgment. Never replace the source claim or perform product, persona, TAM, architecture, or investment analysis.

## Run the workflow

1. Validate `schema/input.schema.json` and lock `project_id` plus `product_version`.
2. Normalize Product, User, and Investment outputs through `src/adapters.mjs` into Claim, Evidence, and SupportLink records.
3. Run deterministic identity, citation, existence, traceability, source-independence, time, scope, metric, E0-E5, and final-gate rules.
4. Consume only short semantic observations through the `semantic_analysis` input or an injected `semanticAnalyzer`. Never persist private model reasoning.
5. Emit exactly `PASS`, `DOWNGRADE`, `REQUEST_MORE_EVIDENCE`, or `REJECT` for every claim.
6. Merge semantic duplicates into CanonicalClaims and Cross-Agent Issues. Count both `agent_count` and independent sources; repeated citations never increase strength.
7. Separate mutually incompatible facts into Conflict records and compatible but decision-relevant positions into DecisionTension records.
8. Build SupervisorHandoff only from the calibrated ledger. REJECT never enters accepted claims; REQUEST enters evidence gaps only.
9. Render summary and full HTML from the same `structured_output_digest`.

Use LaunchScope E0-E5 exactly: E0 team statement, E1 public material, E2 automated or simulated result, E3 real interview/survey/usability evidence, E4 behavior/retention, E5 payment/contract/repeat revenue. Simulated user work is always capped at E2.

## Runtime

```powershell
node packages/evidence-grounding-audit/runner/cli.mjs < input.json
node packages/evidence-grounding-audit/scripts/generate-demo.mjs
node --test packages/evidence-grounding-audit/tests/*.test.mjs
node packages/evidence-grounding-audit/scripts/package-skill.mjs
```

Read `knowledge/rules.v1.md` before changing verdict or source-independence behavior. Inputs and outputs are defined in `schema/`. The runtime makes no network, provider, database, or business-state writes.
