# LaunchScope generation-aware acceptance boundary

Local unit, contract, PostgreSQL integration, MinIO, package, script, and recorded-browser tests prove only their
named paths. They do not prove a provider, browser target, search account, Matrix deployment, or live AgentTeams
execution. Recorded fixtures, API interception, direct model calls, and manually constructed state are never live
end-to-end evidence.

## Manifest-pinned topology

Acceptance resolves topology from each Run's frozen Manifest:

- legacy Runs retain the historical 1+5 identities, including `geo-policy-trend`;
- generation v4 (`architecture_generation=supervisor-1p4-v1`, `agent_contract_generation=v4`) requires exactly
  `evaluation-manager`, `user-evidence`, `product-engineering`, `business-investment`, and `evidence-auditor`;
- the v4 supervisor is the sole Team Leader, peer scheduling is disabled, and no v4 Task or Worker may use
  `geo-policy-trend`;
- v4 Task count is plan-derived. Do not validate it against the legacy fixed seven-Task graph.

## Live generation-v4 completion

A sanitized Run-linked bundle may claim live generation-v4 completion only when it proves all of the following:

- official AgentTeams v1.2.0 Controller/Manager and the five selected v4 Workers are online;
- the supervisor produced a valid ManagerPlan, the control plane materialized the three isolated domain Tasks,
  and the independent Auditor ran serially after their legal terminal states;
- any `NEEDS_MORE` path used at most one targeted remediation and one re-audit;
- RocketMQ Outbox rows are `PUBLISHED`, Inbox dedupe facts exist, and Matrix assignment/completion receipts bind
  the expected tenant, Run, Task, sender role, Worker, and transaction;
- a real allowlisted Chromium capture and a real search result persist as Evidence with source, region, fetch time,
  validity, object reference, and SHA-256;
- AuditResultV3, deterministic score/Decision, ManagerSynthesisV1, Report, and Project Dossier were committed before
  the Run became `COMPLETED`;
- model and tool usage reconcile under the frozen call/token/tool limits. `EXACT` cost mode additionally requires
  USD reservation/consumption/release and provider-cost reconciliation; `TOKEN_ONLY` records cost as unavailable
  and makes no billing-reconciliation claim;
- PostgreSQL and MinIO survive the prescribed service restart and the same browser can reopen the Run, Evidence,
  audit, report, and dossier without duplicate Tasks, tools, or charges;
- disabling `LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED` blocks new v4 admission while completed v4 and historical legacy
  Runs remain readable without conversion.

The minimum live acceptance set is one clear golden Run and one deliberately incomplete-input browser case. The
second case must demonstrate the single supervisor conversation, required clarification and resume, ordinary
supplemental information affecting only unstarted Tasks, and approval before a material scope/cost/permission
change can submit externally.

`SUBMISSION_UNKNOWN`, unknown required token usage, unknown billing in `EXACT` mode, a paid timeout with uncertain
provider state, or any unexplained `PENDING`/`CLAIMED` fact makes the correct conclusion `NEEDS_ATTENTION`; do not
retry, fail over, resubmit, replace, or settle with raw SQL. In `TOKEN_ONLY`, known submission/result plus known
call/token usage may advance without a provider charge amount; persist `model_cost_unavailable` and do not claim
billing reconciliation. Without the authorized URL and required model/search credentials, the correct result is
`BLOCKED_NO_AUTHORIZED_CASE`.

Export a body-free evidence index with `scripts/export-v01-acceptance.py`. Despite its compatibility filename, the
exporter validates each Manifest generation independently and includes v4 brief, plan, ticket, delivery, audit,
synthesis, dossier, clarification, approval, usage, budget, Outbox, Inbox, Matrix, Evidence, and report indexes.
`snapshot-metadata.json` states the proof components per Run; its `external_e2e_claim` remains false unless every
required persisted component is present. Runtime-config and provider-receipt model identity evidence must be
captured separately because it is not inferred from the database Manifest.
## LaunchScope v2.2 Recorded report acceptance

The v2.2 report route is accepted in Recorded mode only when the browser evidence covers all of the following without calling an Agent, model, search provider, or paid external service:

1. a first prediction and same-input rerun omit comparison copy;
2. a comparable repeat shows the index delta after stage and before confidence;
3. a scoring-standard change shows a warning without an index delta;
4. the supervisor report and four specialist routes render from hash-verified canonical documents;
5. summary/full views keep the same report SHA, Claim IDs, Citations, and source directory;
6. a no-login share token reaches only its supervisor report, four specialist reports, and Run-scoped Evidence;
7. the one-button public disclosure is committed before the first upload and is not requested again for the same ProductVersion;
8. individual PDF downloads and the complete ZIP contain five report PDFs, HTML/JSON source directories, and a manifest; optional verified Evidence originals have their own integrity index.

This is **Recorded browser acceptance**, not proof of live AgentTeams, Matrix, network search, or paid-model execution. Live acceptance remains `BLOCKED_NO_AUTHORIZED_CASE` until an authorized case and the required external credentials, budget, and billing controls are supplied.
