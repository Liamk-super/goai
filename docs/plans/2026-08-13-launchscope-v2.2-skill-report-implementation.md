# LaunchScope v2.2 Skill and Report Architecture Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade the existing supervisor 1+4 pipeline so every new report has a stable repeat-prediction baseline, claim-level citations, one canonical supervisor/specialist document, no-login public Demo Evidence access, and deterministic PDF/package exports.

**Architecture:** Extend the current generation-v5 PostgreSQL/object-store control plane through additive v6 contracts and migrations. Deterministic scoring, Evidence audit, report validation, and atomic commit remain authoritative; React, public Demo pages, summary/full views, PDF, and ZIP are projections of the same immutable documents.

**Tech Stack:** Python 3.11/FastAPI/SQLAlchemy/Alembic/PostgreSQL, MinIO/S3-compatible object storage, Next.js 15/React 19/TypeScript, AgentTeams packages, JSON Schema/OpenAPI, Playwright Chromium, PowerShell startup scripts.

---

## Execution rules

- Work in the existing shared workspace only after re-checking `git status`; never reset, clean, stash, overwrite, or reformat unrelated files.
- `reference/`, released contract files, and `packages/contracts/tests/` are frozen. Create new versioned files.
- Implement one task at a time with focused tests before the implementation.
- Do not stage or commit unless the user explicitly authorizes Git actions. If authorized later, use one narrow commit per task.
- A failed PDF/export render never triggers an Agent/model rerun.
- Preserve the current PostgreSQL volume and `/projects` Demo workspace.
- Report verification must distinguish unit/contract, PostgreSQL integration, Recorded browser, and authorized Live evidence.

## Task 1: Freeze v2.2 contracts and report profile

**Files:**

- Create: `packages/contracts/reports/citation-source.v1.json`
- Create: `packages/contracts/reports/report-comparison.v1.json`
- Create: `packages/contracts/reports/specialist-report.v2.json`
- Create: `packages/contracts/reports/supervisor-report.v2.json`
- Create: `packages/contracts/manager/manager-synthesis.v2.json`
- Create: `packages/contracts/audit/audit-result.v4.json`
- Create: `packages/contracts/score/score-profile.v2.json`
- Create: `packages/contracts/score/profiles/full-potential.v2.json`
- Create: `packages/contracts/manager/run-manifest.v6.json`
- Create: `packages/contracts/manager/agents/evaluation-manager.v6.yaml`
- Create: `packages/contracts/manager/agents/user-evidence.v6.yaml`
- Create: `packages/contracts/manager/agents/product-engineering.v6.yaml`
- Create: `packages/contracts/manager/agents/business-investment.v6.yaml`
- Create: `packages/contracts/manager/agents/evidence-auditor.v6.yaml`
- Create: `tests/contracts_v6/test_report_v22_contracts.py`
- Create: `tests/contracts_v6/test_report_v22_hash_locks.py`

**Step 1: Write failing contract tests**

Test exact requirements:

```python
def test_critical_verified_claim_requires_citation(supervisor_schema, valid_report):
    valid_report["claims"][0]["status"] = "VERIFIED"
    valid_report["claims"][0]["decision_relevance"] = "CRITICAL"
    valid_report["claims"][0]["citation_ids"] = []
    assert first_error(supervisor_schema, valid_report).json_path == "$.claims[0]"


def test_pending_claim_may_have_no_citation_but_is_not_score_bearing(supervisor_schema, valid_report):
    claim = valid_report["claims"][0]
    claim.update(status="PENDING_VALIDATION", citation_ids=[], score_bearing=False)
    assert list(validator(supervisor_schema).iter_errors(valid_report)) == []
```

Also assert:

- recommendation enum is exactly `PROCEED`, `VALIDATE_FURTHER`, `ADJUST`, `PAUSE`;
- no probability field or `ABANDON` exists;
- comparison delta is forbidden unless status is `COMPARABLE`;
- RunManifest v6 has five Workers, no geography Worker, `report_profile` and exact contract hashes;
- summary/full is not represented as two report bodies.

**Step 2: Run tests and verify failure**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/contracts_v6
```

Expected: FAIL because v6 schemas do not exist.

**Step 3: Add minimal schemas and identities**

Use the structures in `2026-08-13-launchscope-v2.2-skill-report-design.md` sections 7 and 10. Keep all objects `additionalProperties: false` unless the existing RunManifest compatibility pattern requires otherwise. Calculate and pin content hashes; never copy old hashes.

`full-potential.v2.json` must keep the current 30/30/30/10 index weights unless a separate scoring decision is approved. Add deterministic coverage/confidence rules without calling them probability.

**Step 4: Run focused tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/contracts_v6 packages/contracts/tests
```

Expected: new v6 tests PASS and all frozen tests PASS without source edits.

## Task 2: Add report-v2 persistence and baseline-compatible migration

**Files:**

- Create: `apps/api/migrations/versions/0031_report_v22.py`
- Modify: `apps/api/src/launchscope_api/infrastructure/db/schema.py`
- Modify: `apps/api/tests/integration/test_migrations.py`
- Create: `apps/api/tests/integration/test_report_v22_schema.py`

**Step 1: Write failing migration tests**

Assert the upgraded database contains:

- `evaluation_run.input_snapshot_sha256` and `content_fingerprint_sha256`;
- optional full-evaluation `baseline_run_id` while recheck Runs still require it;
- `evidence_source_locator`;
- `report_claim_citation`;
- `public_demo_disclosure_acceptance`;
- `public_demo_share`;
- `report_export_artifact`;
- tenant composite foreign keys, RLS policies, and unique idempotency keys.

Add a negative test that cross-tenant `baseline_run_id` is rejected.

**Step 2: Run and confirm failure**

```powershell
.venv\Scripts\python.exe -m pytest -q apps/api/tests/integration/test_migrations.py apps/api/tests/integration/test_report_v22_schema.py
```

Expected: FAIL on missing revision/tables.

**Step 3: Implement additive migration**

Use revision id `0031_report_v22` (under 32 characters). Replace only the old check constraint semantics:

```sql
CHECK (run_kind <> 'USER_EVIDENCE_RECHECK' OR baseline_run_id IS NOT NULL)
```

Do not remove historical columns or rewrite Report/Evidence bodies. For existing Run rows, keep new hashes nullable during migration; new v2.2 admission requires them at application level. Add a future tightening note rather than guessing a hash for old Runs.

**Step 4: Run migration tests**

Expected: PASS on clean upgrade and existing-volume upgrade fixtures.

## Task 3: Bind a stable prior Run at full-evaluation admission

**Files:**

- Create: `apps/api/src/launchscope_api/modules/supervisor/baseline_application.py`
- Modify: `apps/api/src/launchscope_api/modules/project_dossier/persistent_application.py:450-541`
- Modify: `apps/api/src/launchscope_api/modules/supervisor/intake_application.py`
- Create: `apps/api/tests/unit/test_report_baseline_selection.py`
- Modify: `apps/api/tests/api/test_project_version_flow.py`
- Modify: `apps/api/tests/integration/test_supervisor_requirement_brief_postgresql.py`

**Step 1: Write the baseline decision table as tests**

```python
@pytest.mark.parametrize(
    ("prior_status", "same_content", "standard_changed", "expected"),
    [
        (None, False, False, "FIRST_EVALUATION"),
        ("COMPLETED", False, False, "COMPARABLE"),
        ("COMPLETED", False, True, "STANDARD_CHANGED"),
        ("COMPLETED", True, False, "SAME_INPUT_RERUN"),
        ("FAILED", False, False, "FIRST_EVALUATION"),
    ],
)
def test_baseline_selection(...): ...
```

Also test that a later Run does not modify the already bound `baseline_run_id`.

**Step 2: Implement canonical input hashing**

Create pure functions:

```python
def input_snapshot_sha256(document: Mapping[str, object]) -> str: ...
def content_fingerprint_sha256(document_without_random_ids: Mapping[str, object]) -> str: ...
```

Inputs must include confirmed ProductProfile, MaterialSelection SHA, selected Material hashes, user-validation script SHA when present, and evaluation mode. Sort keys and lists whose order is non-semantic.

**Step 3: Bind the prior Run during `start_run()`**

Select the latest prior completed `FULL_EVALUATION` for the same Project with committed Decision/Report. Persist the prior id once. Do not select a baseline from failed, partial, later, or another Project Run.

**Step 4: Run focused tests**

```powershell
.venv\Scripts\python.exe -m pytest -q apps/api/tests/unit/test_report_baseline_selection.py apps/api/tests/api/test_project_version_flow.py apps/api/tests/integration/test_supervisor_requirement_brief_postgresql.py
```

## Task 4: Persist human-readable source locators

**Files:**

- Modify: `apps/api/src/launchscope_api/modules/evidence/mcp_application.py`
- Modify: `apps/api/src/launchscope_api/modules/evaluation/dispatch_application.py`
- Modify: `apps/api/src/launchscope_api/modules/evaluation/clarification_application.py`
- Modify: `apps/api/src/launchscope_api/modules/user_validation/application.py`
- Create: `apps/api/src/launchscope_api/modules/evidence/source_locator.py`
- Create: `apps/api/tests/unit/test_evidence_source_locator.py`
- Modify: `apps/api/tests/integration/test_mcp_evidence_ledger.py`

**Step 1: Write failing tests for three source types**

- browser Evidence stores final URL, title, fetch time, region and screenshot hash;
- each public-search result gets a separate locator with canonical URL/title/publisher when available;
- Material Evidence stores file display name and page/section locator without fabricating an external URL.

Test URL canonicalization and `independence_group` deduplication. Two syndications of the same publisher/document cannot count as two independent sources.

**Step 2: Implement `SourceLocatorRepository`**

It accepts already persisted Evidence and inserts append-only locators. Reject a locator whose Evidence belongs to another tenant/Run.

**Step 3: Update Evidence producers**

Do not parse source metadata later from free-form `summary`. Persist locators at Evidence creation while the tool/material context is available.

**Step 4: Run tests**

```powershell
.venv\Scripts\python.exe -m pytest -q apps/api/tests/unit/test_evidence_source_locator.py apps/api/tests/integration/test_mcp_evidence_ledger.py
```

## Task 5: Upgrade audit and scoring admission for citations

**Files:**

- Create: `packages/evidence-grounding-audit/schema/output.v2.1.schema.json`
- Modify: `packages/evidence-grounding-audit/src/index.mjs`
- Modify: `packages/evidence-grounding-audit/src/report-renderer.mjs`
- Modify: `packages/evidence-grounding-audit/prompts/system.md`
- Create: `packages/skills/manifests-v3/evidence-grounding-audit/2.1.0.json`
- Modify: `apps/api/src/launchscope_api/modules/supervisor/audit_application.py`
- Modify: `apps/api/src/launchscope_api/modules/supervisor/completion_application.py:122-199`
- Create: `apps/api/tests/unit/test_report_v22_scoring_admission.py`
- Modify: `apps/api/tests/integration/test_supervisor_audit_postgresql.py`

**Step 1: Write tests proving unsupported Claims cannot score**

Cases:

- no Evidence ref → `NEEDS_MORE` or `REJECTED`, `score_input` ignored;
- Evidence exists but no supporting locator → can support internal-material Claim, but public-market Claim remains pending;
- duplicated publisher links count as one independent source;
- expired Evidence lowers confidence/coverage according to profile;
- `DOWNGRADED` keeps weaker wording and reduced score only when it still has valid support.

**Step 2: Implement AuditResultV4 mapping**

Persist support strength, independent-source count, freshness, referenced Evidence ids, and source-locator ids. Do not let the audit runtime rewrite source Finding text.

**Step 3: Update deterministic scoring filter**

The engine accepts score input only from audited Findings whose citation status is score-bearing. Keep index weights under `full-potential.v2`; supervisor text is never an input.

**Step 4: Run Node and Python tests**

```powershell
pnpm.cmd --dir packages/evidence-grounding-audit test
.venv\Scripts\python.exe -m pytest -q apps/api/tests/unit/test_report_v22_scoring_admission.py apps/api/tests/integration/test_supervisor_audit_postgresql.py
```

## Task 6: Compute comparison, Evidence coverage, and conclusion confidence

**Files:**

- Create: `apps/api/src/launchscope_api/modules/supervisor/report_metrics.py`
- Create: `apps/api/src/launchscope_api/modules/supervisor/report_comparison.py`
- Modify: `apps/api/src/launchscope_api/modules/supervisor/completion_application.py`
- Create: `apps/api/tests/unit/test_report_metrics.py`
- Create: `apps/api/tests/unit/test_report_comparison_v2.py`

**Step 1: Write pure-function tests**

Cover:

- weighted required-dimension Evidence coverage;
- independent-source/freshness/conflict confidence components;
- exact Low/Medium/High thresholds from profile;
- comparable index/dimension deltas;
- no delta under `STANDARD_CHANGED`;
- resolved/unchanged/new risks based on audited identifiers, not prose similarity.

**Step 2: Implement deterministic calculators**

Return typed documents that validate against `report-comparison.v1.json` and the score profile. Never call a model.

**Step 3: Freeze the comparison before synthesis**

Build it from the bound prior Run and current audited/score state. Pass the immutable document/reference into the manager synthesis context. Reject a manager response that changes any comparison value.

**Step 4: Run tests**

```powershell
.venv\Scripts\python.exe -m pytest -q apps/api/tests/unit/test_report_metrics.py apps/api/tests/unit/test_report_comparison_v2.py
```

## Task 7: Commit ManagerSynthesisV2 and SupervisorReportDocumentV2

**Files:**

- Create: `apps/api/src/launchscope_api/modules/supervisor/report_v2.py`
- Modify: `apps/api/src/launchscope_api/modules/supervisor/completion_application.py:400-540,783-814`
- Modify: `apps/api/src/launchscope_api/modules/supervisor/planning_application.py`
- Modify: `scripts/build-agentteams-packages.py`
- Create: `apps/api/tests/unit/test_supervisor_report_v2_validation.py`
- Modify: `apps/api/tests/integration/test_supervisor_audit_postgresql.py`
- Modify: `apps/orchestrator/tests/test_supervisor_1p4_resources.py`

**Step 1: Write failing validation tests**

Reject:

- critical Claim with missing/unknown Citation;
- Citation to Evidence outside the Run;
- verified Claim backed only by rejected Evidence;
- action referring to unknown Claim;
- supervisor-supplied index, confidence, comparison or recommendation that differs from the deterministic inputs;
- first prediction containing a comparison section;
- product title/data not matching the target Run.

**Step 2: Implement `SupervisorReportV2Builder`**

The builder combines:

```text
authoritative Decision + deterministic score/metrics + comparison snapshot
+ validated ManagerSynthesisV2 + Agent report catalog + Citation catalog
```

It emits canonical JSON, stores it privately, validates SHA, then uses the existing atomic Decision/Report/Dossier completion transaction.

**Step 3: Update AgentTeams package instructions**

The manager returns only ManagerSynthesisV2 JSON. Explicitly prohibit market/legal facts not present in audited context and prohibit manual JSON fallback after renderer failure.

**Step 4: Run tests**

```powershell
.venv\Scripts\python.exe -m pytest -q apps/api/tests/unit/test_supervisor_report_v2_validation.py apps/api/tests/integration/test_supervisor_audit_postgresql.py apps/orchestrator/tests/test_supervisor_1p4_resources.py
```

## Task 8: Produce one SpecialistReportDocumentV2 per Agent

**Files:**

- Create: `packages/product-technical-audit/` with `SKILL.md`, `package.json`, `schema/`, `prompts/`, `src/`, `runner/`, and tests
- Create: `packages/business-investment-assessment/` with the same production structure
- Create versioned user report output under `packages/user-validation-designer/schema/`
- Modify: `packages/user-validation-designer/src/presentation.mjs`
- Create versioned EGA report output under `packages/evidence-grounding-audit/schema/`
- Create: `packages/skills/manifests-v3/skill-manifest.schema.json`
- Create: `packages/skills/manifests-v3/user-validation-designer/<version>.json`
- Create: `packages/skills/manifests-v3/product-technical-audit/<version>.json`
- Create: `packages/skills/manifests-v3/business-investment-assessment/<version>.json`
- Create: `packages/skills/manifests-v3/evidence-grounding-audit/<version>.json`
- Modify: `packages/skills/src/launchscope_skills/registry.py`
- Modify: `scripts/build-agentteams-packages.py`
- Create: `packages/skills/tests/test_report_v22_skill_registry.py`
- Modify: `apps/orchestrator/tests/test_agentteams_v12_resources.py`

**Step 1: Write cross-Agent conformance tests**

For all four documents assert:

- one canonical JSON body per Agent/revision;
- stable Claim IDs and Citation IDs;
- summary selector and full selector use the same source SHA;
- no embedded iframe/static HTML truth;
- product name/Run id match assignment;
- no demo placeholder names;
- market/legal/competition Claims require source metadata;
- audit default labels have a user-facing mapping.

**Step 2: Promote selected v2.1 methods, not files**

Use the read-only reference as behavioral input. Reimplement validated stage gates, tables, unit economics, source independence, and action structures against LaunchScope contracts. Do not copy `generate-demo.mjs` or its manual JSON fallback.

**Step 3: Package real runtimes**

Update the package builder so product and investment Workers receive their production runtime directories plus LaunchScope binding instructions. Preserve five-Worker topology and assigned-material-only restrictions.

**Step 4: Run package tests**

```powershell
pnpm.cmd --dir packages/user-validation-designer test
pnpm.cmd --dir packages/evidence-grounding-audit test
pnpm.cmd --dir packages/product-technical-audit test
pnpm.cmd --dir packages/business-investment-assessment test
.venv\Scripts\python.exe -m pytest -q packages/skills/tests/test_report_v22_skill_registry.py apps/orchestrator/tests/test_agentteams_v12_resources.py
```

## Task 9: Add versioned report and public Demo read APIs

**Files:**

- Create: `packages/contracts/openapi/report-experience.v2.yaml`
- Create: `packages/contracts/openapi/agent-reports.v5.yaml`
- Create: `packages/contracts/openapi/public-demo-report.v2.yaml`
- Modify: `apps/api/src/launchscope_api/modules/experience/api.py`
- Modify: `apps/api/src/launchscope_api/modules/experience/read_model.py`
- Create: `apps/api/src/launchscope_api/modules/experience/public_share.py`
- Create: `apps/api/tests/unit/test_report_v2_read_model.py`
- Create: `apps/api/tests/unit/test_public_demo_report_v2.py`
- Modify: `apps/api/tests/integration/test_t10_experience_postgresql.py`

**Step 1: Write API tests**

Assert:

- v2 report returns product stage, conditional comparison, confidence breakdown, four Agent previews and source directory;
- Agent detail returns one canonical document, not separate summary/full bodies;
- public token opens supervisor, four Agent reports and Evidence from the same Run;
- wrong token, revoked token, another Run/report/Evidence all return 404;
- integrity mismatch returns 409/needs attention according to the existing policy;
- legacy endpoints and v4 Agent report contract remain unchanged.

**Step 2: Implement relationship-scoped share resolution**

Replace exact-resource environment-list assumptions only for v2 public routes. Resolve a hashed token to one Run, then validate child relationships in PostgreSQL. Never trust a `run_id` copied from the URL alone.

**Step 3: Load the canonical supervisor body with integrity verification**

The current `report_by_id()` reconstructs a projection from Decision/Synthesis tables. The v2 endpoint must instead load `report.object_key`, verify object metadata and SHA-256 exactly like the Agent report endpoint, parse `SupervisorReportDocumentV2`, and then attach only bounded access URLs/projection metadata. It must not silently rebuild a different body when the object is missing or corrupt.

**Step 4: Keep read projections bounded**

Agent summary list returns preview fields only. Load full object bodies only on the independent report page. Batch Citation/source metadata for a Report in one query.

**Step 5: Run tests**

```powershell
.venv\Scripts\python.exe -m pytest -q apps/api/tests/unit/test_report_v2_read_model.py apps/api/tests/unit/test_public_demo_report_v2.py apps/api/tests/integration/test_t10_experience_postgresql.py
```

## Task 10: Add one-button public disclosure before upload

**Files:**

- Modify: `apps/web/src/app/(workspace)/projects/[projectId]/new-evaluation/page.tsx:300-369,835-855`
- Create: `apps/web/src/components/forms/PublicDemoDisclosure.tsx`
- Modify: `apps/web/src/lib/api-client.ts`
- Modify: `apps/web/src/lib/evaluation-draft-session.ts`
- Modify: `apps/api/src/launchscope_api/modules/project_dossier/api.py`
- Modify: `apps/api/src/launchscope_api/modules/project_dossier/persistent_application.py`
- Create: `apps/api/tests/unit/test_public_demo_disclosure.py`
- Create: `apps/web/tests/unit/public-demo-disclosure.test.ts`

**Step 1: Write tests around current immediate-upload behavior**

Test that selected files do not call `uploadMaterial` until disclosure succeeds. One click records acceptance and resumes the existing queue. Reloading the same ProductVersion does not prompt again. A failed acceptance keeps files local and unuploaded.

**Step 2: Implement the minimal modal**

Copy exactly:

```text
公开 Demo：上传材料可能在报告 Evidence 链中公开展示。
[我已了解，继续上传]
```

Do not add a checkbox, per-file confirmation, material classifier, or long agreement page. Keep the existing model-egress `externalConsent` separate.

**Step 3: Record acceptance idempotently**

The API requires `Idempotency-Key` and `X-Correlation-Id`, stores policy version and timestamp, and later binds the Run.

**Step 4: Run tests**

```powershell
.venv\Scripts\python.exe -m pytest -q apps/api/tests/unit/test_public_demo_disclosure.py
pnpm.cmd --filter @launchscope/web test
```

## Task 11: Build the supervisor v2.2 report UI

**Files:**

- Create: `apps/web/src/components/reports/v2/SupervisorReportV2.tsx`
- Create: `apps/web/src/components/reports/v2/ReportTopCard.tsx`
- Create: `apps/web/src/components/reports/v2/InlineCitation.tsx`
- Create: `apps/web/src/components/reports/v2/CitationDetails.tsx`
- Create: `apps/web/src/components/reports/v2/AgentReportCards.tsx`
- Create: `apps/web/src/components/reports/v2/ReportActions.tsx`
- Modify: `apps/web/src/components/reports/SupervisorLayeredReport.tsx`
- Modify: `apps/web/src/lib/api-client.ts`
- Modify: `apps/web/src/lib/i18n.ts`
- Modify: `apps/web/src/app/(workspace)/globals.css`
- Create: `apps/web/tests/unit/supervisor-report-v22.test.ts`

**Step 1: Write presentation tests**

Assert the exact top-card order. First prediction must not render a comparison label anywhere. Comparable runs show `before → after` and delta. Standard-changed runs show the warning without delta.

Assert:

- visible name is `爆款潜力指数`, never “爆率/概率”;
- confidence is primary and Evidence coverage is supporting copy;
- four recommendation labels map to current enums;
- dimensions explain additions/deductions without allowing edit;
- all critical Claims have visible inline citation buttons;
- four specialist cards appear at the bottom.

**Step 2: Add a schema-version branch**

`SupervisorLayeredReport` uses the v2 component only when the API returns `report_schema_version === "2.0"`. Legacy reports keep the existing formatter and layout.

**Step 3: Reuse current design tokens**

Use existing `plate`, `bearing`, `status-pill`, cream/brass/ink variables, focus styles and mobile breakpoints. Do not add an unrelated dashboard theme.

**Step 4: Run tests and typecheck**

```powershell
pnpm.cmd --filter @launchscope/web test
pnpm.cmd --filter @launchscope/web typecheck
```

## Task 12: Upgrade independent specialist pages and public routes

**Files:**

- Create: `apps/web/src/components/reports/v2/SpecialistReportV2.tsx`
- Create: `apps/web/src/components/reports/v2/SpecialistViewTabs.tsx`
- Modify: `apps/web/src/components/reports/AgentReportsPanel.tsx`
- Modify: `apps/web/src/app/(workspace)/runs/[runId]/agent-reports/[agentCode]/page.tsx`
- Create: `apps/web/src/app/(public)/shared/demo/[token]/runs/[runId]/agent-reports/[agentCode]/page.tsx`
- Create: `apps/web/src/app/(public)/shared/demo/[token]/runs/[runId]/evidence/[evidenceId]/page.tsx`
- Modify: `apps/web/src/components/reports/PublicDemoShell.tsx`
- Create: `apps/web/tests/unit/specialist-report-v22.test.ts`
- Modify: `apps/web/tests/unit/public-demo-share.test.ts`

**Step 1: Write navigation/view tests**

Assert:

- supervisor cards navigate in the current tab and remain normal anchors for Ctrl/Cmd click;
- child page `summary/full` tabs read the same `content_sha256` and Claim IDs;
- back link targets `/reports/{reportId}#agent-reports`;
- no iframe exists;
- Evidence audit uses Chinese labels by default and puts raw codes in `<details>`;
- public pages do not read/create a local Demo session.

**Step 2: Refactor parsing out of `AgentReportsPanel`**

Move canonical document normalization into a typed library/component. Keep `DomainAgentReportViewV1` as a legacy adapter only.

**Step 3: Implement stable Evidence viewer links**

Public Citation links go to the public Evidence viewer, which displays metadata and requests a short-lived raw read URL. Dangerous HTML is downloaded or sandboxed, not executed in the LaunchScope origin.

**Step 4: Add no-index metadata**

Set public layout metadata to `robots: { index: false, follow: false }`. Do not label this as confidentiality.

**Step 5: Run tests**

```powershell
pnpm.cmd --filter @launchscope/web test
pnpm.cmd --filter @launchscope/web typecheck
```

## Task 13: Generate deterministic PDF and complete report packages

**Files:**

- Create: `apps/api/src/launchscope_api/modules/decision_report/export_application.py`
- Create: `apps/api/src/launchscope_api/modules/decision_report/export_renderer.py`
- Modify: `apps/api/src/launchscope_api/modules/experience/api.py`
- Create: `packages/contracts/openapi/report-export.v1.yaml`
- Create: `apps/api/tests/unit/test_report_export_application.py`
- Create: `apps/api/tests/integration/test_report_export_objects.py`
- Create: `apps/web/src/components/reports/v2/ReportExportActions.tsx`
- Modify: supervisor and specialist v2 components to expose print-ready state
- Create: `apps/web/tests/unit/report-export-actions.test.ts`

**Step 1: Write cache and integrity tests**

Assert the export key includes source SHA, renderer version, locale, view and `include_evidence`. A repeated request returns the same artifact without launching Chromium. Source SHA change creates a new artifact.

Test ZIP paths such as `../../escape` are sanitized. Test missing Evidence produces a manifest entry and no empty fake file.

**Step 2: Implement print rendering**

Use the existing Playwright dependency. Open the public Demo print route, wait for a bounded `data-report-ready="true"`, then call Chromium PDF. Disable external navigation other than already rendered Citation links. Do not invoke a model.

**Step 3: Implement package assembly with Python stdlib `zipfile`**

Default package: five PDFs, `来源目录.html`, `来源目录.json`, and `manifest.json`. Optional package adds verified Evidence originals and `evidence-index.json`.

**Step 4: Implement API and UI**

All POSTs require idempotency/correlation headers. Public Demo calls are token scoped and rate bounded. UI offers individual PDF plus `一键下载完整报告包` and `同时下载证据原件`.

**Step 5: Run tests**

```powershell
.venv\Scripts\python.exe -m pytest -q apps/api/tests/unit/test_report_export_application.py apps/api/tests/integration/test_report_export_objects.py
pnpm.cmd --filter @launchscope/web test
```

## Task 14: Complete compatibility, startup, and browser acceptance

**Files:**

- Modify if required after inspection: `start.ps1`, `start.cmd`
- Modify if required: `scripts/demo-bootstrap.ps1`
- Modify if required: `scripts/demo-preflight.ps1`
- Modify if required: `scripts/demo-start.ps1`
- Modify if required: `scripts/demo-stop.ps1`
- Modify: `.env.demo.example`
- Modify: `docs/demo/acceptance.md`
- Modify: `docs/demo/runbook.md`
- Create: `tests/e2e/test_report_v22_recorded.py`
- Modify: `apps/web/tests/e2e/launchscope-v01.spec.ts`

**Step 1: Run all focused suites first**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/contracts_v6 apps/api/tests/unit apps/api/tests/integration
pnpm.cmd -r --if-present test
pnpm.cmd -r --if-present typecheck
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy .
docker compose -f infra/compose/docker-compose.yml config
```

Expected: PASS, except explicitly environment-gated Live tests remain skipped with a stated reason.

**Step 2: Verify frozen boundaries**

Hash/byte-compare all pre-existing published contracts, `packages/contracts/tests/`, and `reference/` inputs against the pre-task snapshot. Expected: unchanged.

**Step 3: Run required startup smoke**

```powershell
./start.ps1 -Mode Recorded -NoBrowser
```

Verify Web, Ops, and API HTTP responses. Do not reset the database or create a replacement Demo tenant to make it pass.

**Step 4: Run Recorded browser scenarios**

Cover at 1440×900 and mobile width:

1. first prediction, no comparison;
2. comparable repeat prediction;
3. standard-changed repeat prediction;
4. same-input rerun;
5. supervisor detailed report and four current-tab child routes;
6. summary/full parity;
7. inline public/internal Citation opens;
8. no-login public supervisor, Agent and Evidence pages;
9. disclosure appears once;
10. PDF/ZIP downloads and source manifest.

Call this **Recorded browser acceptance**, not Live E2E.

**Step 5: Optional authorized Live acceptance**

Only after an authorized case, valid model/search/Matrix credentials, price/budget limits and preflight pass. Verify real 1+4 delivery, Evidence provenance, citations, usage/billing, restart readback and no unknown state. Any `SUBMISSION_UNKNOWN` or unknown billing stops the run without automatic retry.

## Completion checklist

- [ ] ADR 0020 and all new contract versions are present and hash locked.
- [ ] No existing contract/reference/frozen test was edited.
- [ ] Full Run admission binds a stable prior Run and content fingerprint.
- [ ] First/same-input/standard-changed/comparable display rules pass.
- [ ] Unsupported critical Claims cannot score or support recommendations.
- [ ] Supervisor and four Agent reports each have one canonical immutable body.
- [ ] Summary/full/Web/PDF share score, recommendation, Claim IDs and citations.
- [ ] Public Demo requires no login and validates Run-scoped token relationships.
- [ ] Disclosure is one button and occurs before upload.
- [ ] Public Evidence viewer and originals are reachable from valid citations.
- [ ] Five PDFs and optional-Evidence ZIP are deterministic and integrity indexed.
- [ ] Legacy reports and Agent artifacts remain readable.
- [ ] Focused tests, typecheck, build, migration, startup and Recorded browser acceptance pass.
- [ ] Final handoff states exact proof level and any Live blocker.

## Execution handoff

The plan is designed for sequential execution in the current dirty workspace with a status/diff review between tasks. Do not start implementation, create a worktree, stage, commit, push, deploy, or run paid external calls without the user's explicit authorization.
