# LaunchScope Benchmark V1

Benchmark V1 is a provider-neutral evaluation boundary for model selection, single-Agent behavior, the AgentTeams
1+5 graph, evidence calibration, Prompt/RAG/Skill regression and V1/V2 comparison. It is not a collection of tests
written to make the current implementation pass.

## Canonical layout

```text
benchmarks/
  schemas/                 Case, Suite, Run Manifest, Score and human-review contracts
  registry/                immutable source hashes and traceable normalized rules
  suites/model/            30 model cases, including three public anonymous anchors
  suites/agents/           80 cases across the six Agent identities
  suites/system/           12 orchestration and fail-closed system cases
  blind/                   sealed alias assignment and unblinding governance
  holdout/                 governance manifest only; sealed inputs stay outside Git
  adapters/promptfoo/      exact-pinned model/single-Agent adapter
  adapters/observability/  default-off Studio and AgentLoop descriptors
  policy.v1.json           counts and release gates
  coverage.v1.json         long-term evaluation goals mapped to suites, cases and locked sources
```

The native package in `packages/benchmark` is the only canonical validator and scorer. Adapters consume Cases and
emit Run Manifests; they cannot redefine Oracle or Rubric.

## Case and run separation

Every Case has:

- `input`: inline synthetic scenario or a SHA-256-locked verbatim reference file;
- `oracle`: `RULE_DERIVED`, `CONTRACT_DERIVED`, or `HUMAN_VERIFICATION_REQUIRED`;
- `rubric.automated`: exact deterministic assertions and critical-gate flags;
- `rubric.human`: named dimensions and reviewer instructions;
- `metadata`: Agent, difficulty, language, tags, partition and contamination status.

Runs are not embedded in Cases. A Run Manifest records provider/model, implementation hashes, case IDs, source-lock
digest, repeat index, cache/network mode, outputs and usage. A Score Report preserves critical violations and pending
human work rather than collapsing everything into one score.

Every Run also has an execution lane. `MODEL_API` is a direct provider rule test, `SINGLE_AGENT` exercises one Agent
identity, and `AGENTTEAMS_E2E` is the real 1+5 system path. They are never interchangeable. Live runs record requested
and observed models by Case or Worker; an absent/mismatched runtime identity is a critical violation.

## Automatic versus human evaluation

Automatic scoring is appropriate for schema validity, exact action/status, routing, question/persona/action bounds,
required or forbidden fields, rule citations, source hashes, evidence/status gates, DAG ordering, retry policy and
version locks. Human evaluation is required for real-world rankings, download/adoption claims, subjective reasoning
quality, conflict judgment, actionability and end-to-end usefulness. An LLM judge may assist a human rubric later but
does not convert external facts into Gold.

The anonymous T-TRANS-01, SOCIAL-HARD-01 and ACADEMIC-HARD-01 inputs are public contamination-prone anchors. Their
original files stay under `reference/`, are never copied or normalized, and have no fabricated ranking Gold. The
catalog requires the explicit `HUMAN_VERIFICATION_REQUIRED` sentinel and independent primary-source review.

## Minimum V1 scale

| Layer | Cases | Rationale |
|---|---:|---|
| Model | 30 | covers calibration, abstention, scope, safety and the three difficult public anchors without making provider runs prohibitively expensive |
| Agent | 80 | manager and auditor receive 16 each because they own critical gates; four specialists receive 12 each for rule and adversarial coverage |
| System E2E | 12 | covers intake, 1+5 ordering, evidence admission, conflict, budget, unknown submission, state authority, frozen versions and live/recorded separation |
| Sealed holdout | 25 planned | roughly 20% of the public/development catalog; kept outside the repository to reduce contamination and overfitting |

## Local commands

Use the repository Python and include the two workspace packages when they are not editable-installed:

```powershell
$env:PYTHONPATH = "packages/benchmark/src;packages/observability/src"
.venv\Scripts\python.exe -m launchscope_benchmark validate
.venv\Scripts\python.exe -m pytest packages/benchmark/tests -q
.venv\Scripts\python.exe -m ruff check packages/benchmark
.venv\Scripts\python.exe -m mypy packages/benchmark/src
.venv\Scripts\python.exe -m launchscope_benchmark self-test --suite system-e2e-v1 --output artifacts/benchmarks/system-self-test.json
.venv\Scripts\python.exe -m launchscope_benchmark export-promptfoo --case-set formal-model-selection --output artifacts/benchmarks/model-matrix-promptfoo-cases.json
```

The self-test uses `canonical-oracle-provider`, makes no model/network call and proves only the harness path. It is not
LaunchScope system quality evidence.

Promptfoo requires Node 24 LTS for this benchmark. The root application retains its existing runtime range.

Install its isolated adapter dependency without changing the root workspace lockfile:

```powershell
pnpm.cmd --dir benchmarks/adapters/promptfoo --ignore-workspace install --frozen-lockfile --no-optional
```

```powershell
pwsh -File scripts/verify-benchmark-v1.ps1
```

The approved formal API matrix uses exactly `MODEL-EVD-01` and `MODEL-EVD-02` for all three candidates, three uncached
repeats each, and validates the `model` field returned by every provider response:

```powershell
pwsh -File scripts/run-benchmark-model-matrix.ps1 -AuthorizePaidCalls
```

This is 18 paid API calls and is intentionally separate from real AgentTeams. Do not rerun it merely to reproduce an
already preserved result. For a live 1+5 run, capture and verify the six active Worker models before accepting the E2E:

```powershell
pwsh -File scripts/verify-agentteams-benchmark-model.ps1 -ExpectedModel kimi-k3
```

Prompt/RAG/Skill changes use two manifests from the same ordered Case set:

```powershell
.venv\Scripts\python.exe -m launchscope_benchmark compare-regression baseline.json candidate.json --output regression.json
```

Formal provider comparisons must run at least three independent repeats with `--no-share --no-cache`; provider cost
and credentials require separate authorization. Ordinary CI, validation, self-test, export, regression comparison and
runtime-identity verification cost USD 0; `run-benchmark-model-matrix.ps1` is the explicit paid exception.

## Release gates and anti-contamination

- schema and source hash validity: 100%; critical violations: 0;
- ordinary deterministic assertions: at least 95%; each Agent: at least 90%; manager/auditor critical rules: 100%;
- required human reviews must be resolved before a model or system release decision;
- holdout inputs and Gold remain externally sealed, use independent ownership and rotate after exposure;
- blind human review hides provider, model, Prompt/Agent version and run order until all required judgments are locked;
- do not change production Prompt/Agent and benchmark Oracle in the same change without independent owner review;
- current production output and historical anonymous reports are never Gold;
- recorded output, Mock, health checks and oracle-provider self-tests cannot satisfy live E2E;
- without an authorized external Case the live status is `BLOCKED_NO_AUTHORIZED_CASE`.
