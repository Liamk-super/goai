# LaunchScope v2.2 Dynamic Report Quality Remediation Implementation Plan

> **For Codex:** REQUIRED SKILLS: Use `architecture-designer`, `tdd`, `test-runner`, `frontend-design`, and `executing-plans` as applicable. Implement task-by-task; do not stop after producing another plan.

**Goal:** Make database-backed LaunchScope reports reach the information depth, citation integrity, language consistency, and visual hierarchy already specified for v2.2, without mutating historical report artifacts or weakening the 1+4/control-plane authority boundaries.

**Architecture:** Keep PostgreSQL as business truth, immutable object-store report documents as presentation truth, and deterministic scoring/audit as the only source of authoritative values. Add versioned report/manager/API contracts when required, then project the same canonical document into workspace Web, public Demo, PDF, and ZIP. Preserve v2 historical readers and artifacts.

**Tech Stack:** Python/FastAPI/SQLAlchemy/JSON Schema, PostgreSQL, object storage, Next.js/TypeScript, Playwright, pytest, Vitest/Node tests, PowerShell startup chain.

---

## 1. Mandatory references

Read these before editing:

1. `AGENTS.md`
2. `docs/adr/0001-frozen-boundaries-and-change-policy.md`
3. `docs/adr/0010-supervisor-agent-one-plus-four-generation.md`
4. `docs/adr/0012-supervisor-one-plus-four-only-admission-and-agent-reports.md`
5. `docs/adr/0020-report-v22-baseline-citations-public-demo.md`
6. `docs/plans/2026-08-13-launchscope-v2.2-skill-report-design.md`
7. `docs/plans/2026-08-13-launchscope-v2.2-skill-report-implementation.md`
8. `apps/web/src/components/reports/demo/HitPredictorDemoReport.tsx`
9. `apps/web/src/lib/hit-predictor-demo-data.ts`
10. Current v2 report contracts, builders, readers, export pipeline, and presentation components.
11. `D:/life apps/history for WeChat/xwechat_files/wxid_2j9jtf3072fn12_9fa1/msg/file/2026-08/爆款预测器_前端改版需求文档_工程执行版_V1.0.docx`
12. `docs/plans/2026-08-15-hit-predictor-frontend-v1-goal-brief.md`

The static `/demo/hit-predictor` page is a presentation/reference sample only. Do not copy its static facts into a real Run, and do not call it database-backed or Live evidence.

The Word document governs frontend product language, interaction flow, page hierarchy, priority, and acceptance. ADRs, versioned contracts, deterministic scoring, audit rules, and immutable report boundaries govern business truth when a frontend request could otherwise change semantics.

## 2. Read-only baseline to preserve

Before editing, inspect and record the current behavior of:

- supervisor report `c5fbd39b-5937-4f15-a3b8-fee160566830`;
- Run `3ff153ee-5064-4dee-8898-2fe3f0498c2c`;
- all four specialist report routes;
- the corresponding v2 API documents and canonical SHA values.

Known audit facts from 2026-08-15, to re-check rather than blindly assume:

- supervisor: index 28, stage `DRAFT`, recommendation `PAUSE`, confidence 0.69, displayed coverage 100%;
- 5 supervisor Claims, 4 actions, no highlight/critical-issue/cross-domain section Claims;
- 117 source locators and 5 Citations, but only one unique cited source locator;
- all supervisor Citations are `DOWNGRADED`, while two Claims are labelled `VERIFIED`;
- specialist Claim counts were user 1, product 5, investment 6, auditor 3;
- generated prose mixes Chinese and English;
- specialist full view does not render `domain_payload`, `risks`, `audit_summary`, or `raw_audit_refs`.

Do not overwrite the historical report object, its SHA, Decision, Synthesis, Evidence, or database rows to make the demonstration look better.

## 3. Non-negotiable boundaries

- Preserve the dirty worktree. Never reset, clean, stash, overwrite unrelated changes, or kill an unverified process.
- Do not edit anything under `reference/` or the frozen architecture baseline.
- Do not edit published contract files in place. Breaking semantic or structural changes require an ADR plus new versioned contract files and compatible readers.
- Keep the physical topology 1+4: Supervisor plans/synthesizes; user/product/investment run in parallel; Evidence Auditor runs serially.
- Supervisor cannot research, alter audit outcomes, choose authoritative scores, or invent a recommendation.
- PostgreSQL remains business truth; immutable object-store documents remain report presentation truth.
- `PENDING_VALIDATION`, unknown billing, `SUBMISSION_UNKNOWN`, or uncertain external effects never enter scoring and never trigger automatic retry.
- Do not stage, commit, push, deploy, reset the Demo workspace, replace the tenant, or clear PostgreSQL data.
- Do not perform paid/external Live calls. L1-L4 evidence is required; L5 remains separately authorized.
- Do not make a report longer by inventing facts. Missing evidence must remain an explicit gap with a verification action.

## 4. Target user experience

The dynamic supervisor report must project the same information hierarchy as the approved v2.2 design:

1. prediction target, title, navigation, export/share;
2. hit-potential index, product stage, conditional comparison, conclusion confidence, evidence-coverage explanation, recommendation;
3. four dimension scores plus cited positive/negative score drivers;
4. concise headline and readable comprehensive conclusion;
5. highlights and critical issues, including truthful empty states when evidence does not support an item;
6. user/product/investment role summaries;
7. Supervisor cross-domain synthesis, conflicts, and unresolved uncertainty;
8. actions with owner, deadline, success criteria, failure triggers, required Evidence, and related Claims;
9. cited Evidence/source directory and collapsed audit details;
10. four independently useful specialist summary cards.

Each specialist page must use one canonical document for summary/full views and render:

- executive summary and metrics;
- role-specific structured analysis;
- Claims with inline Citations;
- risks and evidence gaps;
- actions and retest gates;
- cited source directory;
- human-readable audit summary and collapsed internal references.

Use the current cream/gold/dark report language and the static Demo's hierarchy as inspiration, but keep dynamic components typed and data-driven.

## 5. Architecture decisions required before implementation

### Decision A: Contract versioning

Inspect whether each requirement is additive and backward-compatible. Because existing v2 files are published, create the next available ADR (expected `docs/adr/0024-...md`) and new versioned contracts for any breaking requirement.

At minimum, the canonical supervisor document must persist:

- locale/report language;
- dimension scores;
- positive and negative score-driver Claim IDs;
- required section identities or typed section projections;
- an explicit evidence-coverage label/definition version;
- comparison and confidence profile references.

The specialist contract must no longer accept an arbitrary minimally populated `domain_payload` as a complete professional report. Define role-specific typed payloads or discriminated schemas for user, product, investment, and Evidence audit reports. Require meaningful summary, analysis, risks/gaps, and action structures without forcing unsupported positive facts.

### Decision B: Claim and Citation strength

Define and enforce a monotonic evidence-strength rule. A Claim must not appear stronger than all supporting Citations. A critical factual sentence containing independently provable clauses must be split into separately cited Claims or have Citations covering each clause.

Search-result metadata is not automatically substantive Evidence. It may remain in the Evidence ledger, but the canonical report source directory should contain only cited support/counter/background sources that passed the appropriate extraction and audit policy.

### Decision C: Locale

Freeze locale in Run/report identity, pass it to every specialist and Supervisor task, validate the final document language, and preserve Claim/Citation identity across views and export. Do not translate business enums or authoritative values; translate only presentation and generated prose through an auditable path.

### Decision D: Coverage semantics

Do not label required-dimension coverage as generic `Evidence completeness`. Keep evidence coverage, evidence quality, independent-source support, freshness, and conflicts distinct. Version any scoring/profile semantic change rather than silently changing historical results.

## 6. Implementation tasks

### Task 1: Freeze failing acceptance tests

**Files:**

- Add focused tests under `packages/contracts/tests/`, `apps/api/tests/unit/`, `apps/api/tests/integration/`, and `apps/web/tests/unit/` using the repository's current conventions.
- Add or extend a Playwright scenario in `apps/web/tests/e2e/launchscope-v01.spec.ts` only where browser behavior cannot be covered by unit tests.

Write failures for:

- missing dimension scores/drivers in the canonical document;
- a `VERIFIED` Claim backed only by `DOWNGRADED`/weaker Citations;
- uncited search-result spam entering the user-visible source directory;
- sparse specialist documents passing as complete;
- mixed-language specialist output under a frozen Chinese locale;
- specialist full view hiding typed domain content, risks, and audit summary;
- current historical v2 documents remaining readable and SHA-stable.

Run each focused test and record the expected failure before implementation.

### Task 2: Add ADR and versioned contracts

Create the next ADR and new contract files; never edit released v2 schemas or frozen contract tests. Add hash locks and new tests according to existing contract conventions.

Update versioned OpenAPI resources and run/report profile references only as required. Keep legacy v1/v2 endpoints and readers compatible.

### Task 3: Strengthen specialist production

Update the user, product, investment, and Evidence-audit report production paths so every Agent emits its typed canonical report in the frozen locale.

Ensure material context includes successfully parsed/OCR/vision-inspected units with page/section locators. If selected material remains uncovered, fail closed or represent the coverage gap; never describe unread PDFs as analyzed.

Do not increase prompt length without bounded output and size handling. Preserve the 2 MB artifact boundary and explicit overflow failure behavior.

### Task 4: Strengthen audit, scoring, synthesis, and commit

Implement:

- Claim/Citation status compatibility;
- citation coverage for every score-bearing/critical Claim;
- relevant and deduplicated source-directory construction;
- deterministic dimension scores and score-driver projection;
- versioned coverage/confidence semantics;
- complete but evidence-bounded Manager synthesis sections;
- atomic validation before object-store/database commit.

The Supervisor may explain deterministic values but cannot author them.

### Task 5: Add compatible read APIs

Read committed canonical objects through object metadata plus SHA verification. Add versioned workspace/public readers for the new document while preserving old report URLs and historical v2 reads.

Return compact four-card summaries without serially downloading all full specialist documents.

### Task 6: Build the dynamic presentation

Create a typed new-version presentation path or compatible renderer without converting the static Demo into the production reader.

Fix:

- long conclusion typography;
- Chinese/plain-language labels and statuses;
- dimension values and cited drivers;
- required supervisor sections and truthful empty states;
- specialist summary cards;
- specialist `?view=summary|full` deep-link state;
- typed domain payload, risks, audit summary, and source presentation;
- cited sources first, with uncited research material omitted or explicitly collapsed;
- keyboard, mobile, reduced-motion, and print behavior.

Do not expose internal IDs/hashes/enums outside collapsed audit details.

### Task 7: Keep Web/public/PDF/ZIP deterministic

Make workspace Web, public Demo, specialist views, PDF, and ZIP project from the same canonical source SHA. Preserve token/run ownership checks, ZIP path sanitization, evidence inclusion rules, renderer cache keys, and no-model export behavior.

Do not claim export is complete until PDF and ZIP contents have been downloaded, parsed, and compared with Web Claim IDs, citations, scores, and recommendations.

### Task 8: Compatibility and startup verification

Run focused contract, API, integration, Web unit, typecheck, accessibility/layout, and export tests.

If migrations, environment variables, API routes, dependencies, or startup behavior change, inspect and update the full startup chain required by `AGENTS.md`, then run:

```powershell
./start.ps1 -Mode Recorded -NoBrowser
```

Verify Web, Ops, and API HTTP responses and run Chromium checks at desktop and narrow widths. Preserve the existing PostgreSQL volume and Demo workspace.

## 7. Acceptance gates

The goal is complete only when all applicable gates pass:

1. Historical report `c5fbd39b-...` still reads from the same immutable object/SHA; no business rows were rewritten.
2. A new-version deterministic fixture or authorized local Recorded Run renders every required supervisor section.
3. Four dimension values and cited positive/negative drivers come from the canonical report, not another mutable endpoint.
4. Summary text is readable prose, not a full paragraph rendered as a heading.
5. Required sections never silently disappear; unsupported sections show a truthful state instead of invented content.
6. Four specialist pages are independently useful and summary/full share the same canonical SHA and Claim IDs.
7. Role-specific structured content, risks/gaps, actions, sources, and audit summary are visible in full view.
8. All generated report prose matches the frozen locale; internal enums/IDs/hashes stay in audit detail.
9. Critical and score-bearing Claims have valid, semantically compatible Citations.
10. The user-visible source directory contains no mass of uncited search results; sources are deduplicated and labelled by role/status.
11. Coverage is not presented as generic 100% completeness when independent support or quality is partial.
12. First evaluation has no comparison DOM; comparable/standard-changed/same-input behavior remains deterministic.
13. Workspace and public views enforce access boundaries and display the same business facts.
14. PDF and ZIP are downloaded and parsed; scores, recommendation, Claim IDs, and citation labels match Web.
15. Focused L1-L4 evidence passes, including Recorded startup/browser checks when startup-affecting files changed.
16. No result is described as Live E2E without separately authorized L5 AgentTeams/model/search/browser plus usage/billing/artifact evidence.

## 8. Execution and reporting behavior

- Continue until the implementation and applicable acceptance gates are complete; do not stop after design or TODOs.
- Fix small in-scope bugs directly. For a large architectural conflict, preserve evidence and report the exact blocker before expanding scope.
- If a full test suite times out, run focused suites, inspect reports/process identity, and state the unverified boundary. Do not kill an unverified process.
- If a real external submission/billing state becomes unknown, stop that path and mark it `NEEDS_ATTENTION`; never retry automatically.
- Final handoff must list changed files, commands/results, browser evidence, remaining risks, and the precise Recorded/Live boundary.
