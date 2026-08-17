# ADR 0005: Govern Benchmark V1 as an implementation-independent evaluation boundary

- Status: Accepted for Benchmark V1
- Date: 2026-08-09
- Scope: model, Agent, system and holdout evaluation
- Supersedes: none

## Context

LaunchScope needs repeatable evidence for model selection, single-Agent behavior, the 1+5 dispatch graph, evidence
calibration, Prompt/RAG/Skill regression, V1/V2 comparison and final system effectiveness. Existing tests protect
contracts and deterministic application behavior, but they do not define a quality dataset, a human-review protocol,
contamination controls or a provider-neutral run record.

The benchmark must not make the production implementation its oracle. Market rankings, downloads, adoption and
other external facts cannot become Gold without independent verification. The three supplied anonymous model inputs
must remain byte-for-byte read-only public anchors. The frozen architecture also requires versioned Run Manifests,
real E2E evidence, explicit cost authorization and telemetry that excludes Prompt and business bodies.

## Decision

1. `benchmarks/schemas`, `benchmarks/registry` and `benchmarks/suites` are the canonical Benchmark V1 data boundary.
   The native `launchscope-benchmark` Python package is the canonical validator and scorer. Production Prompt, Agent
   code, provider adapters and current outputs are never an oracle source.
2. A Case separates immutable input, typed Oracle, automated and human Rubric, and Metadata. Run results live in a
   separate versioned Run Manifest. `RULE_DERIVED` and `CONTRACT_DERIVED` Oracles require traceable rule identifiers.
   Any label that needs external truth is `HUMAN_VERIFICATION_REQUIRED`; automated checks may score structure and
   calibration, but not invent the missing fact.
3. Source locks record repository-relative paths and SHA-256 digests for the architecture baseline, six Agent
   knowledge bases and three anonymous public anchors. Validation fails closed on any mismatch. The anchors are
   referenced in place with `verbatim: true`; no copied or normalized substitute is created.
4. V1 contains 30 model cases, 80 Agent cases (16 manager, 12 each for four specialists, 16 auditor), and 12 system
   cases. Public/development cases remain inspectable. A sealed holdout manifest reserves 25 case slots outside this
   repository; it records governance and a digest only after an independent owner supplies the sealed set.
5. Critical rule violations are gates, not points that can be averaged away. Reports keep deterministic checks,
   pending human judgments, latency, token and cost dimensions separate. Benchmark Gold and production Prompt/Agent
   changes may not be approved in the same change without independent benchmark-owner review and a version bump.
6. Promptfoo `0.121.19` is an exact-pinned adapter for model and single-Agent matrices only. Benchmark use requires
   Node 24 LTS, `sharing: false`, `--no-share`, and `--no-cache` for formal independent repeats. Promptfoo does not own
   Case, Oracle, Rubric, Run Manifest, system orchestration or acceptance policy.
7. OpenTelemetry remains the observation boundary. AgentScope Studio is the local competition-oriented trace view;
   its descriptor remains disabled until a pinned compatible deployment proves ingestion and display. Alibaba Cloud
   AgentLoop is a disabled-by-default OTLP/dataset-export adapter for later continuous evaluation. It may not be
   enabled, upload traces or incur cost without explicit authorization. EvalScope and AgentScope Evaluation/OpenJudge
   remain documented alternatives, not V1 dependencies.
8. Recorded and oracle-provider runs verify the harness only. They never satisfy live AgentTeams, browser/search,
   AgentLoop or paid-model E2E. Without an authorized external case the live gate is
   `BLOCKED_NO_AUTHORIZED_CASE`.
9. Execution evidence is split into `MODEL_API`, `SINGLE_AGENT` and `AGENTTEAMS_E2E` lanes. A live Run must record
   requested and observed model identities from the provider response or six live AgentTeams Worker resources.
   Missing or mismatched identity is a critical violation; package/config inspection is preparation evidence only.
10. The formal V1 model parity set is `MODEL-EVD-01` plus `MODEL-EVD-02`, run uncached for three independent repeats
    against `kimi-k3`, `glm-5.2` and `qwen3.8-max`. Direct API parity cannot be reported as AgentTeams E2E.
11. Human review of the three public anchors and final system effectiveness is blind. Provider, model, Prompt/Agent
    version and run order remain externally sealed until required judgments and source checks are locked.

## Non-functional requirements

- Reproducibility: exact dependency versions, canonical JSON serialization, source digests and repeat indexes.
- Security and privacy: no secrets, Prompt, messages, evidence body, report body or private reasoning in telemetry;
  no network access in default validation or deterministic self-test.
- Reliability: malformed schemas, unknown rules, source drift and unauthorized live modes fail closed.
- Performance: the 122-case catalog must validate locally in under 10 seconds on the supported developer baseline.
- Maintainability: adapters consume canonical cases and emit canonical run/score artifacts; they cannot redefine Gold.
- Cost: ordinary CI spends USD 0 and makes no paid calls. Costs and unknown usage are explicit Run Manifest fields.

## Consequences

### Positive

- Model, Agent and system regressions share one traceable vocabulary without coupling to a provider or Prompt layout.
- Rule-derived expectations can be audited back to immutable knowledge sources.
- Human truth gaps remain visible rather than becoming fabricated benchmark accuracy.
- Competition observability can be added without exporting business bodies or replacing PostgreSQL business truth.

### Negative

- Maintaining rule locators, hashes and human reviews adds governance work.
- A sealed holdout requires an independent owner and storage channel outside the repository.
- Promptfoo and Studio add a second language/runtime and local operational surface.

### Failure modes and mitigations

| Failure | Mitigation |
|---|---|
| Knowledge source changes silently | SHA-256 lock failure; deliberate versioned rule-registry update |
| Benchmark overfits current Prompt | sealed holdout, metamorphic families, independent Gold review |
| Human label is treated as fact | required `HUMAN_VERIFICATION_REQUIRED` mode and pending review state |
| Adapter uploads sensitive content | default-off exporters plus existing OTel allowlist and body guard |
| Cached provider response masks variance | formal Promptfoo runs use `--no-cache` and explicit repeat index |
| Recorded run is presented as live | separate execution mode and `BLOCKED_NO_AUTHORIZED_CASE` gate |
| Provider alias or stale Worker masks the real model | requested/observed identity map plus live-response/Worker source gate |
| API rule test is presented as system quality | mutually exclusive execution lanes and separate reports |

## Alternatives considered

- Promptfoo as the whole benchmark: rejected because it does not own LaunchScope system DAG, evidence semantics,
  holdout governance or human factual verification.
- EvalScope as the V1 core: deferred. It is useful for general model and Agent benchmarks but overlaps the approved
  Promptfoo adapter and is not required for the competition stack.
- AgentScope Evaluation/OpenJudge as the runner: rejected for V1 because LaunchScope executes through AgentTeams;
  adding a second Agent execution abstraction would blur acceptance evidence. Studio remains a visualization option.
- AgentLoop as the sole system: rejected for local canonical truth because it introduces cloud account, data-transfer
  and cost dependencies. It remains the preferred optional upper-layer continuous-evaluation adapter.

## Acceptance

The repository verifier must prove schema validity, exact counts, unique IDs, valid rule references, protected source
hashes, deterministic scoring, telemetry redaction, Promptfoo configuration safety and zero-network default behavior.
Live AgentTeams/Studio/AgentLoop/paid-model claims require separately authorized runtime evidence.

## References

- `docs/势能引擎技术架构基线_V1.0.md` sections 23, 24, 28, 29 and 31
- `docs/adr/0001-frozen-boundaries-and-change-policy.md`
- `docs/adr/0002-p0-skill-set-reconciliation.md`
- [Promptfoo configuration](https://www.promptfoo.dev/docs/configuration/guide/)
- [Alibaba Cloud AgentLoop overview](https://help.aliyun.com/zh/cms/cloudmonitor-2-0/agentloop-overview)
