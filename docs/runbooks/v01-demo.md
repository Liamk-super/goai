# LaunchScope V0.1 local demo

The local demo is a deterministic, zero-cost read-only acceptance path. It
uses the real FastAPI API, PostgreSQL/RLS control plane, private MinIO objects,
durable SSE cursor, Evidence/Finding/Audit/Decision/Report chain and redacted
Ops projection. It uses the fixed 1+5 identity contracts and structured local
handoffs, but does **not** claim a live AgentTeams/Matrix server, external LLM,
public-research provider or paid call.

1. Start the pinned PostgreSQL and MinIO services and apply Alembic migrations.
2. Start API with `LAUNCHSCOPE_ENABLE_LOCAL_DEMO_EXECUTION=true` and
   `LAUNCHSCOPE_DEMO_FIXTURE_ROOT` pointing to `tests/e2e/fixtures`.
3. Start Web with the API base plus the local tenant/actor session values.
4. Create a Project and V1, upload `v1/product-materials/brief.md`, answer the
   five gap fields, confirm the profile, plan, then run the local read-only
   evaluation.
5. Open the Run timeline, Evidence chain, Report, redacted Ops projection and
   repeat with V2 before opening Compare.

The exact environment variables and local tenant bootstrap command are in the
root README. A `.md` fixture is sent as `text/markdown`, uploaded directly to
MinIO, and accepted only after the server-side HEAD agrees with its immutable
size, MIME and SHA-256 metadata. Evidence reading follows the inverse boundary:
tenant authorization first, then a short-lived signed GET.

The local execution button is deliberately visible only while a Run is
`PLANNED`. It performs no paid call and no public network research. Its fixed
repository fixture, 1+5 identity hashes, Task DAG, handoffs, audit decisions,
zero-cost budgets and report metadata are frozen into PostgreSQL/object storage
for replay.

Use `pwsh -File scripts/verify-v01.ps1 -Environment Test -BudgetLimit 0` to
write a body-free test summary and hashes under `artifacts/acceptance/`.

External acceptance remains blocked unless an authorized main-case URL and
credentials are supplied. Do not set the external URL merely to make a skipped
test green. `SUBMISSION_UNKNOWN` or unknown billing must remain frozen without
retry, provider switch, resubmission or manual settlement.
