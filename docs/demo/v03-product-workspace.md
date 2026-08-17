# V0.3 product workspace implementation note

## Implemented user flow

1. Create or switch a durable Project.
2. Open the four-ring project workbench and select one of four intake sections.
3. Enter structured fields, a URL, free text, or a private file.
4. Optionally send free text to the configured OpenAI-compatible provider after explicit consent.
5. Review and edit the MODEL_INFERENCE draft; no inferred value is a confirmed fact.
6. Upload a JSON intake snapshot (and optional original file) through the private object flow.
7. Generate only the remaining critical gap questions, record `unknown` without fabrication, confirm the profile, and create a durable Run.
8. Observe the real PostgreSQL/SSE/AgentTeams projection, evidence counts, tool summaries, failures and approval requirements.
9. Read the calibrated four-dimension report and submit a new version for same-standard regression.

## Real and fallback boundaries

- Real: Demo tenant/workspace, Project, ProductVersion, quarantined upload, ProductProfile draft/confirmation, EvaluationRun, SSE cursor, AgentTeams projection, Evidence, report, and V1/V2 comparison are PostgreSQL-backed runtime APIs.
- Real when configured: `/api/v1/intake:extract` calls the environment-owned OpenAI-compatible provider. It is bounded to 30,000 input characters and 800 output tokens, requires tenant headers plus explicit external-processing consent, and returns a draft only.
- Deterministic local fallback: `execute-local-demo` and recorded snapshot remain visibly labelled and are not provider/AgentTeams E2E proof.
- Not executed in this change: the paid `Dispatch real AgentTeam` action. It retains the existing USD 20 Run cap and fail-closed unknown-submission policy.

## Open-source reuse decision

The existing Next.js, FastAPI, Pydantic, AgentTeams, Matrix, RocketMQ, PostgreSQL, SSE and MCP stack remains the base. Historical Runs retain the fixed 1+5 projection; generation v4 uses the supervisor 1+4 projection resolved from its frozen Manifest. The ring and state drawer stay lightweight because adding CopilotKit/AG-UI or React Flow now would duplicate the frozen state protocol. For the next conversational/approval phase, prefer an adapter to `assistant-ui` or AG-UI/CopilotKit; for a user-editable dynamic task DAG, prefer React Flow; for more complex state transitions, prefer Motion. Do not build those general-purpose primitives from scratch.

## Verification performed

- Provider `/v1/models` compatibility and one synthetic `qwen3.8-max` JSON extraction.
- Real browser: Demo identity, empty state, Project creation, four-ring workbench, four intake sections, 100% profile review, private object upload, durable profile confirmation, and PLANNED Run.
- Desktop viewport 1440x900 and mobile viewport 390x844; mobile `scrollWidth == clientWidth` and no browser console errors.
- Python full suite, Ruff, mypy, web tests, TypeScript typecheck and Next.js production build.

External AgentTeams/provider task execution, policy/trend refresh and a completed live V2 comparison still require an explicitly approved paid Run and authorized external case.
