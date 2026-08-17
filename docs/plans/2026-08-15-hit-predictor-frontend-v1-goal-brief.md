# Hit Predictor Frontend V1 Goal Brief

## Purpose

Implement the engineering-execution requirements in `爆款预测器_前端改版需求文档_工程执行版_V1.0.docx` together with the v2.2 dynamic-report quality remediation, without turning a frontend redesign into a rewrite of AgentTeams, Skills, scoring, audit, or persisted business truth.

This brief is the concise execution index. Detailed report contract, evidence, export, and compatibility work is in:

- `docs/plans/2026-08-15-launchscope-v22-report-quality-remediation-goal.md`
- `docs/plans/2026-08-13-launchscope-v2.2-skill-report-design.md`
- `docs/plans/2026-08-13-launchscope-v2.2-skill-report-implementation.md`
- `docs/adr/0020-report-v22-baseline-citations-public-demo.md`

## Authority order

When requirements conflict, use this order:

1. `AGENTS.md`, frozen-boundary policy, security/fail-closed invariants;
2. published versioned contracts and accepted ADRs;
3. the v2.2 report design and its new versioned ADR/contract extensions;
4. the Word document for user-facing names, interaction, layout, priority, and acceptance;
5. existing implementation details and visual preferences.

Do not reinterpret frontend wording as authority to alter weights, thresholds, recommendations, persisted enums, Agent topology, or audit outcomes.

## Product language

Use one product promise across homepage, empty states, run pages, reports, and help copy:

> 在你继续投入时间和钱之前，先让一组 AI 创业专家找出产品最可能失败的地方，并告诉你下一步最值得验证的 1-3 件事。

The product is an evidence-based decision and validation tool, not an AI success-probability oracle.

Use these frontend role names consistently:

- 项目负责人
- 目标用户
- 产品经理
- 投资人
- 证据校准

Map display names at the frontend/presentation boundary. Do not rename Skill packages, AgentTeams identities, database enums, task ownership, or contract codes merely to satisfy UI naming.

## Entry and admission behavior

The homepage's first interaction asks the user to describe the product, not to name a project record.

Required elements:

- a large product-description input as the visual center;
- plain-language placeholder explaining what to describe and current stage;
- a primary action such as `开始预评审` or `让专家团队看看`;
- quick stage choices: 想法、原型、Demo、已有用户、已上线;
- material upload/link entry near the input, not hidden behind project creation;
- a visible `我的项目档案` entry.

Admission rules:

- 想法 only: enter idea-incubation/product-definition conversation; do not pretend a full evaluation is possible;
- prototype/clickable draft: conditional lightweight review while collecting target user and validation objective;
- usable Demo/MVP: enter the formal prediction flow;
- real users/live: enter the formal flow and emphasize real user, retention, operation, version, and business evidence.

Do not default every project to a complete full evaluation.

## Conversational intake and project portrait

Collect information like an interview:

- one main question at a time;
- at most two quick choices;
- only ask questions that can change the evaluation;
- clearly mark inferred/uncertain values for user confirmation;
- save each confirmed round so the user can resume;
- explain briefly why a question matters when necessary.

Before a formal Run, display a project portrait confirmation containing:

- one-sentence product;
- current stage;
- target user;
- current validation objective;
- included materials;
- missing information/evidence.

The formal Run starts only after confirmation and normal control-plane admission.

## Wheel and run page

Keep the wheel but bind it to information semantics:

- center: 项目负责人;
- three main sectors: 目标用户、产品经理、投资人;
- the outer/mobile evidence point: 证据校准;
- idle: team introduction;
- running: current persisted task state;
- completed: sector returns to the center;
- audit: evidence calibration follows the outer audit path;
- final report opens only after synthesis and durable completion.

For each role show:

- human-readable status;
- current bounded action;
- completed output summary/evidence count;
- failure, retry, or information-request state when applicable.

It is allowed to show tool category, source count, elapsed time, task state, and whether more material is needed. Do not display chain-of-thought, hidden prompts, long internal reasoning, internal IDs, or English execution payloads to normal users.

Stability is a P0 requirement:

- reserve layout height for asynchronous status;
- use skeletons/placeholders;
- keep wheel, main cards, and report entry anchored;
- prevent status refreshes from recalculating page structure and causing jumps;
- avoid global flashing/reordering during polling or SSE updates.

## Result and report experience

Use `爆款潜力指数`, never success probability.

The concise report first screen contains:

1. index and stage recommendation;
2. one-sentence conclusion;
3. four dimensions with values/strength and evidence level;
4. no more than three ranked P0/P1/P2 problems;
5. one to three actions that can change the decision;
6. links to user, product, investment, and Evidence-calibration reports;
7. a link to the complete analysis.

The complete report contains:

1. decision summary;
2. product and stage;
3. target users;
4. product and Demo flow/completeness;
5. commercial and competitive evidence;
6. verified advantages;
7. core risks;
8. Evidence and confidence;
9. next validation actions and retest gates;
10. four independently readable specialist reports.

The report implementation, canonical fields, Citation rules, source filtering, typed specialist payloads, locale, public Demo, PDF/ZIP, compatibility, and validation gates are defined in `2026-08-15-launchscope-v22-report-quality-remediation-goal.md`.

## Evidence explanation

Every key conclusion needs a `为什么这么判断` entry that reveals a user-readable Evidence drawer or side panel containing:

- source/title;
- collected/fetched time;
- applicable region/version/user scope;
- evidence strength and audit status;
- collection method category;
- conflict/counter-evidence explanation.

Do not expose sensitive URLs or files without authorization. Do not let uncited search-result metadata masquerade as supporting Evidence.

Case comparison is P1 and must include both success and failure cases, maturity stage, financing/commercial evidence, differences, and sources. If reliable comparable cases are unavailable, show an explicit unavailable state instead of fabricating a benchmark.

## Project archive and reevaluation

Rename the user-facing history concept to `项目档案`.

Each project card should show, when available:

- product name/one-sentence product;
- current stage;
- current recommendation;
- hit-potential index;
- conclusion confidence;
- latest formal version;
- latest change summary;
- last evaluation date.

Support search, stage filtering, and updated-time sorting. Do not keep a fixed recent-six limitation.

V1/V2 comparison must answer:

1. which prior problems were resolved;
2. which looked improved but still fail;
3. which Evidence became stronger or weaker;
4. which new risks appeared;
5. why the final recommendation changed or did not change.

Comparison remains bound to immutable prior Run/report identity and compatibility rules; it is not reconstructed from mutable current tables.

## Priorities

### P0: required in this goal

- homepage product-description entry and material/stage shortcuts;
- consistent frontend role names;
- conversational supplementation, persistence, and project portrait confirmation;
- transparent/stable run page;
- result-page hierarchy and P0/P1/P2 problem grading;
- four specialist report entries and complete report entry;
- project archive terminology, complete list, and re-entry;
- no layout jumping, flashing, or unbounded reordering;
- dynamic report integrity and completeness work in the report remediation plan.

### P1: complete after P0 is stable

- expandable Evidence drawer;
- sourced success/failure/competitor comparison;
- V1/V2 comparison visualization;
- project search, filtering, and sorting;
- richer report explanation and specialist navigation.

### P2: do not expand into until P0/P1 pass

- entrepreneurial community;
- incubator case library;
- long-term competitor/policy/product-version monitoring.

P2 is not a reason to stop P0/P1, and is not automatically authorized external integration work.

## Frontend engineering boundaries

- Do not change scoring logic, weights, success/failure thresholds, authoritative recommendations, or audit decisions in frontend code.
- Do not show more evidence quantity by discarding relevance or trust standards.
- Do not hard-code Demo facts into database-backed reports.
- Do not rename backend Skills/contracts/fields simply because frontend role names changed.
- Do not expose internal task IDs, prompts, hashes, model chain-of-thought, or raw English middleware state to ordinary users.
- Do not model P0 as a warning color only; P0 changes whether to continue.
- Treat asynchronous stability, keyboard access, mobile readability, and reduced motion as acceptance requirements.

## Acceptance

The frontend work is accepted only when:

1. a new user can explain what the product does and what to input within five seconds;
2. the homepage no longer uses project name as the only/primary first input;
3. stages are distinguished and idea-only projects do not enter formal full evaluation directly;
4. all user-facing role names are consistent;
5. the user can see what each role is doing, completed, failed, or needs;
6. the result uses hit-potential index and never presents a success probability;
7. the first report screen has at most three ranked problems and clear next actions;
8. key conclusions have an expandable evidence explanation;
9. users can re-enter the same project archive and continue conversation/evaluation;
10. V1/V2 entry and semantics are reserved and compatible with immutable comparison;
11. asynchronous status updates do not visibly jump, flash, or reorder core layout;
12. core flow and report cards remain readable at common mobile widths;
13. dynamic reports satisfy every applicable gate in the report remediation plan;
14. tests and browser evidence distinguish static, unit, Recorded, and Live boundaries truthfully.

