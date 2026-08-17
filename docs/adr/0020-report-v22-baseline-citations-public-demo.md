# ADR 0020: Report v2.2 baselines, citations, and public Demo evidence

- Status: Accepted design; implementation pending
- Date: 2026-08-13
- Decision owners: LaunchScope product and evaluation control plane
- Amends: ADR 0010 supervisor 1+4 generation and ADR 0012 private Agent report access
- Does not change: the physical 1+4 topology, PostgreSQL authority, deterministic scoring authority, or fail-closed policy

## Context

The current generation-v5 LaunchScope flow already commits deterministic scores, a supervisor synthesis, four immutable Agent report artifacts, and Finding-to-Evidence links. The current user report, however, reduces the supervisor output to a short conclusion and three reasons/actions, keeps the detailed evidence chain behind developer mode, and exposes only internal Evidence identifiers. The public Demo share exposes a completed Run and supervisor report but deliberately hides Agent report bodies and Evidence downloads.

The reviewed `reference/launchscope-skills-v2.1/` package improves report composition, dual-layer specialist reading, tables, and action presentation. It does not provide claim-level public citations, and its static HTML/manual JSON fallback conflicts with LaunchScope's immutable structured-result and PostgreSQL authority boundaries.

Product decisions were confirmed one at a time:

- the only name for the top score is **爆款潜力指数**; it is not a probability;
- the supervisor produces a complete detailed report, with four specialist reports as supporting documents;
- a first prediction shows no V1/V2 or “相比上一次” placeholder;
- a later prediction compares against the prior completed formal prediction only when product input changed;
- a changed scoring standard is shown but not numerically compared;
- each specialist produces one audited canonical report, rendered as summary/full views;
- important claims carry inline citations and a complete source list;
- unsupported claims are marked as pending validation and cannot affect the score or authoritative recommendation;
- the Demo requires no sign-in and may publicly expose every submitted Evidence object after one simple disclosure confirmation;
- supervisor and specialist reports can be exported separately as PDF and together as a package;
- authoritative recommendations remain `PROCEED`, `VALIDATE_FURTHER`, `ADJUST`, and `PAUSE`; `ABANDON` is not introduced.

## Decision

### 1. Report v2.2 is a capability-set version, not a shared Skill version

“v2.2” names the coordinated report capability set. Agent Identities, Skills, prompts, score profiles, report contracts, and renderers retain independent semantic versions and SHA-256 hashes. A new Run Manifest generation freezes the exact set. Existing published artifacts are never edited in place.

The external `launchscope-supervisor` reference is mapped to the versioned `evaluation-manager` Identity plus `ManagerSynthesisV2`; it does not become a second stateful manager or scoring authority.

### 2. One canonical document, multiple views

The supervisor and each specialist commit one immutable structured document. Summary/full Web views, print views, PDFs, and packages are deterministic renderings of that same document. They cannot contain separately generated conclusions or scores.

Static HTML/PDF/ZIP files are derived artifacts, not business truth. A renderer failure never authorizes manually invented JSON, placeholder product data, or a second model call.

### 3. Supervisor report and specialist navigation

The supervisor report is the default complete report. Its top card contains, in order:

1. 爆款潜力指数;
2. 当前阶段;
3. 相比上一次, only when a prior changed-product prediction exists;
4. 结论可信度, with Evidence coverage as supporting text;
5. 当前建议.

The report then contains the conclusion, conditional comparison, supported highlights, critical issues, three role views, cross-domain reasoning, action gates, Evidence/source directory, and four specialist summary cards.

Each specialist card links in the current tab to an independent URL. The specialist page switches between summary and full views without fetching or generating a second report. No iframe is used.

### 4. Stable prior-run binding

At full-evaluation admission the control plane computes an immutable input snapshot hash from the confirmed ProductProfile, confirmed MaterialSelection, relevant script/context hashes, and ProductVersion. It finds the most recent prior `COMPLETED` full evaluation for the same Project that has a committed Decision and Report and a different input snapshot hash.

That prior Run is bound once to the new Run. A later Run cannot change the comparison shown by an already committed report.

Comparison states are:

- `FIRST_EVALUATION`: no prior changed-product completed prediction; the UI omits the comparison row and section;
- `COMPARABLE`: the prior Run exists and score/report profile versions are compatible; show score and dimension deltas;
- `STANDARD_CHANGED`: the prior Run exists but standards are incompatible; show “评估标准已更新，暂不可直接比较” and no numeric delta;
- `SAME_INPUT_RERUN`: the input snapshot is unchanged; retain operational history but omit product-improvement comparison.

The existing database check tying `baseline_run_id` exclusively to `USER_EVIDENCE_RECHECK` must be replaced additively so a full evaluation may optionally bind a prior Run while a user-evidence recheck still requires one.

### 5. Score, confidence, and Evidence coverage

The existing deterministic score is presented as **爆款潜力指数**. The supervisor explains its versioned dimensions and contributions but cannot change the total, weights, caps, or recommendation.

Evidence coverage and conclusion confidence remain separate deterministic fields internally:

- Evidence coverage measures how many required decision dimensions have at least one audited, usable Finding with valid Evidence;
- conclusion confidence combines audited Evidence quality, coverage, source independence, freshness, and unresolved-conflict penalties under a versioned profile.

The top card displays one primary field, for example `结论可信度：中`, followed by supporting text such as `证据覆盖 55%，缺少续费数据和独立访谈`. Detailed components remain expandable.

### 6. Claim-level citations and pending-validation claims

Every decision-relevant report sentence is represented as a Claim block with stable `claim_id`, text, decision relevance, status, and citation identifiers. Each citation resolves to an Evidence object and, when applicable, a specific public source locator.

Public-source metadata includes canonical URL, title, publisher, publication/fetch time, locator, region, content hash, independence group, and audit status. Internal Evidence includes display name, page/section locator, object hash, and stable public Demo Evidence-view URL.

A Claim without valid support is `PENDING_VALIDATION`. It may appear in information gaps or validation actions, but it is excluded from deterministic scoring and cannot be the sole basis for an authoritative recommendation. The Evidence Auditor, not the supervisor, promotes or downgrades its status.

Default user language maps audit states to `已验证`, `证据不足`, `需要补充`, and `存在冲突`. Raw enums, Finding IDs, rule IDs, and hashes remain available under audit details.

### 7. Public Demo disclosure and access

Demo report sharing is link-scoped and requires no sign-in. A Run-scoped public share grants read access to its committed supervisor report, four Agent reports, citation metadata, Evidence viewer, Evidence originals, and cached exports. Relationship checks ensure a token for one Run cannot read another Run.

Before the first material upload for a ProductVersion, the UI shows one concise disclosure and one primary button: `我已了解，继续上传`. Acceptance is recorded once with policy version, actor, ProductVersion, timestamp, and later Run binding. It does not classify or prohibit material types.

Public pages declare `noindex, nofollow`; this reduces indexing but is not presented as confidentiality. Production/private access remains unchanged until a future ADR explicitly promotes the Demo access model.

### 8. Deterministic exports

Individual supervisor/specialist PDF exports and the complete report package are keyed by the source report hash, renderer version, locale, view, and Evidence-inclusion option. Repeated requests replay the cached artifact.

The default package contains five PDFs, a source directory, and a manifest. Evidence originals are included only when the caller chooses `同时下载证据原件`. Missing/corrupt Evidence is reported in the manifest; it is never replaced with an empty file.

### 9. Compatibility and failure policy

Historical report documents and Agent artifacts remain readable through their existing projections. New readers branch on schema/report generation rather than guessing from current configuration.

Integrity mismatch, missing committed Evidence, unknown object persistence, or export ambiguity fails closed. It does not trigger a new Agent/model execution, synthetic report generation, manual JSON construction, or automatic external resubmission.

## Consequences

### Positive

- The requested report depth and navigation reuse the existing supervisor and Agent report routes.
- One structured source prevents summary/full/Web/PDF conclusion drift.
- Inline citations become verifiable rather than decorative source counts.
- Repeat-prediction comparisons are stable, replayable, and honest about standard changes.
- Demo evidence is genuinely accessible without login under the explicitly accepted disclosure.

### Negative

- A new contract/report generation, additive migrations, public child-resource authorization, and export cache are required.
- Public Demo links can expose sensitive material by design; the disclosure cannot make the content private.
- Source metadata and claim-level citation validation increase Agent output and audit complexity.
- Historical and v2.2 report readers must coexist.

### Neutral

- The physical 1+4 topology and serial auditor do not change.
- The four recommendation enums do not change.
- Export rendering is an internal deterministic side effect and does not authorize external research or paid model calls.

## Alternatives considered

### Copy the v2.1 static HTML/iframe implementation

Rejected because it creates parallel rendering truth, weakens integrity, and does not fit existing independent report pages.

### Generate summary and full reports separately

Rejected because two model outputs can disagree and double cost without adding business truth.

### Select the comparison baseline at report-read time

Rejected because the same immutable report could change when a newer Run is added.

### Show Evidence coverage and conclusion confidence as equal top-card metrics

Rejected because users experience them as duplicate concepts. Coverage remains visible as the explanation for confidence.

### Add an `ABANDON` recommendation

Rejected for v2.2 because no deterministic irreversible trigger currently exists. Adding it later requires a separate ADR and score-contract generation.

### Keep public Demo Agent reports and Evidence private

Rejected by product decision. The accepted Demo behavior is no-sign-in public access after one simple disclosure.

## Verification gates

- published contracts and frozen contract-test source remain byte-identical;
- new schemas reject uncited critical Claims and accept pending-validation Claims only outside scoring;
- baseline tests cover first, changed-input, same-input, standard-changed, failed-prior, and later-Run stability cases;
- public share tests prove Run-scoped Agent report/Evidence access and cross-Run denial;
- summary/full/Web/PDF projections share Claim IDs, score, recommendation, citations, and source hash;
- export retry is idempotent and corrupt/missing Evidence remains explicit;
- focused Python/Node tests, typecheck, build, migration tests, Recorded startup smoke, and Web/Ops/API HTTP checks pass before handoff;
- Recorded/local/browser proof is reported separately from authorized Live AgentTeams/model/search evidence.

## References

- `docs/adr/0001-frozen-boundaries-and-change-policy.md`
- `docs/adr/0010-supervisor-agent-one-plus-four-generation.md`
- `docs/adr/0012-supervisor-one-plus-four-only-admission-and-agent-reports.md`
- `docs/design/主管Agent_1+4架构设计_V1.md`
- `reference/launchscope-skills-v2.1/` (read-only input)
- [W3C PROV overview](https://www.w3.org/TR/prov-overview/)
- [ALCE: Enabling Large Language Models to Generate Text with Citations](https://aclanthology.org/2023.emnlp-main.398/)
- [NIST AI Risk Management Framework 1.0](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10)
