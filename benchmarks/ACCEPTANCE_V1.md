# Benchmark V1 acceptance record

- Date: 2026-08-09
- Worktree: existing dirty `main`, one commit ahead of `origin/main`
- Git actions: no reset, stash, clean, stage, commit, push or deployment
- Paid/external model calls: four same-case `kimi-k3` AgentTeams runs in total, including three recovery runs on 2026-08-09; no retry after unknown search state

## Implemented scope

| Requirement | Evidence | Status |
|---|---|---|
| Governance decision | `docs/adr/0005-benchmark-v1-governance.md` | PASS |
| Canonical Case/Oracle/Rubric/Run schemas | `benchmarks/schemas/` | PASS |
| Source hash lock | baseline, six knowledge bases, three anchors, six P0 Skill manifests | PASS, 16 locks |
| Traceable Agent rules | `benchmarks/registry/rules.v1.json` | PASS, 60 rules |
| Model benchmark | `benchmarks/suites/model/` | PASS, 30 cases |
| Six-Agent evaluation | `benchmarks/suites/agents/` | PASS, 80 cases with 16/12/12/12/12/16 distribution |
| System E2E evaluation | `benchmarks/suites/system/` | PASS, 12 cases |
| Holdout governance | `benchmarks/holdout/manifest.v1.json` | PASS, 25 externally sealed slots planned |
| Goal-to-case coverage | `benchmarks/coverage.v1.json` | PASS, 9 long-term dimensions |
| Native runner/scorer | `packages/benchmark/` | PASS; subset Case sets, runtime-model gate and regression comparison |
| Human evaluation structure | Case human rubrics plus `human-review.schema.json` | PASS; reviews remain pending |
| Promptfoo adapter | isolated Node 24 package, exact `promptfoo@0.121.19` lock | PASS; offline smoke plus three-model formal matrix |
| Blind review | `benchmarks/blind/manifest.v1.json` and human-review schema | PASS; assignments stay externally sealed |
| AgentLoop adapter | default-off OTLP/data export descriptor; local JSONL only | PASS; cloud disabled |
| OTel privacy boundary | benchmark trace projection uses existing allowlist/body redaction | PASS |
| AgentScope Studio real display | official compatibility check | BLOCKED_NO_DOCUMENTED_GENERIC_OTLP_INGEST |

## Verification evidence

`pwsh -File scripts/verify-benchmark-v1.ps1` with Node 24.14.0:

- catalog: 8 suites, 122 cases, 60 rules, 16 source locks;
- benchmark pytest: 14 passed, including runtime-identity, model-matrix, regression and fail-closed negative controls;
- benchmark ruff: passed;
- benchmark mypy: 9 source files passed;
- deterministic system harness E2E: passed, 12 cases, zero network and USD 0;
- Promptfoo config validation: passed;
- Promptfoo offline dry-run: 2 passed, 0 failed, 0 errors, `--no-share --no-cache`.

Full workspace checks after all changes:

- `.venv\Scripts\python.exe -m pytest -q`: 164 passed, 33 skipped;
- `.venv\Scripts\python.exe -m ruff check .`: passed;
- `.venv\Scripts\python.exe -m mypy .`: 120 source files passed;
- `pnpm -r --if-present typecheck` with install scripts disabled for the existing root supply-chain gate: both Web and Ops passed;
- `pnpm -r --if-present test` with the same constraint: Web 38 passed; Ops has zero discovered tests.

The 33 Python skips are explicitly gated by absent `LAUNCHSCOPE_TEST_DATABASE_URL`, S3 settings, or an authorized
external read-only Case. They are not counted as live acceptance.

## Truth and contamination audit

- T-TRANS-01, SOCIAL-HARD-01 and ACADEMIC-HARD-01 remain verbatim `reference/` inputs referenced by path and SHA-256.
- Their market rankings, adoption and real-user outcomes are `HUMAN_VERIFICATION_REQUIRED`; historical reports are not Gold.
- No production output was used to set an Oracle.
- No Case body is stored for the sealed holdout.
- Ten required human rubric decisions remain pending across public anchors and final-effectiveness review.
- Root `package.json` and root `pnpm-lock.yaml` match their pre-task state; Promptfoo is isolated below the adapter.

## Honest external boundaries

- Studio: official docs expose Studio through AgentScope `studio_url` and generic OTLP backends through
  `tracing_url`. No documented generic Studio OTLP receiver was found for the existing AgentTeams/Collector path, and
  no UI trace was fabricated. See `agentscope-studio-compatibility.md`.
- AgentLoop: no cloud account/resource was created, no trace uploaded and no cost incurred. Local export produced 12
  rows with `uploaded: false` only.
- AgentTeams/browser/search/paid model: the initial authorized `kimi-k3` run produced Leader, specialist, browser and
  search evidence, then correctly stopped at `NEEDS_ATTENTION` after the user-evidence Agent exhausted its browser
  Tool quota. Three same-case recovery runs followed under the standing authorization. The bounded recovery corrected
  the explicit 2-browser/8-search assignment contract, CoPaw iteration capacity and cross-session context isolation.
  The final candidate run `6921d3dc-758d-401a-9c08-6930ef50169d` executed Leader planning, specialist Agents, three
  successful browser captures and one successful search Evidence write, then fail-closed because a later search
  submission or billing state became unknown. No retry or replacement was performed after that state. Auditor and
  final synthesis did not execute, so System E2E remains NOT PASSED. See
  `benchmarks/reports/kimi-k3-agentteams-e2e-20260809.md`.
- Runtime model identity: six live Running AgentTeams Worker resources reported `kimi-k3` and passed
  `scripts/verify-agentteams-benchmark-model.ps1`. Together with the observed run this proves the deployed Worker
  runtime and real orchestration path; it does not prove a clean terminal E2E or a three-model quality win.
- Holdout: an independent benchmark owner must create, seal and later review the 25 hidden Cases before holdout scores
  can be used for release selection.
