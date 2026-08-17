# LaunchScope / 势能引擎 V0.3 Demo

LaunchScope is an evidence-driven product validation control plane. V0.2 adds
a browser-local Demo identity, the frozen AgentTeams v1.2.0 1+5 topology,
RocketMQ 5.x Proxy dispatch, Matrix handoffs, controlled browser/search/context
MCP, deterministic four-dimension synthesis and a USD 20 hard Run limit.

V0.3 adds the light four-ring project workbench, four-section intake studio,
explicit model-assisted extraction drafts, human profile confirmation, critical
gap questions, richer 1+5 Agent projections, calibrated reports and V1/V2
regression surfaces. Model extraction is opt-in per request and never writes a
confirmed ProductProfile by itself.

## Local prerequisites

- Python 3.11–3.13 and a repository `.venv`
- Node.js 20.19+ and pnpm 11.9
- Docker Desktop with Compose

Install the already pinned workspace dependencies:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]" -e packages/domain -e packages/contracts -e packages/skills -e packages/observability -e apps/api -e apps/orchestrator -e apps/worker
pnpm.cmd install --frozen-lockfile
```

Bootstrap once, then fill the generated untracked `.env.demo.local`. Never put
passwords, API keys, Matrix tokens or signed URLs in Git or browser storage:

```powershell
pwsh -File scripts/demo-bootstrap.ps1 -InstallAgentTeams
pwsh -File scripts/demo-preflight.ps1 -RequireExternalCase
pwsh -File scripts/demo-start.ps1
```

For a one-click local start, double-click `start.cmd`. It prepares missing
dependencies, starts Live mode with authorized public search and opens the project list.
An existing browser Demo session is reused; only a browser without a valid
session is redirected to the Demo login page.
Clicking it again safely restarts a previously tracked LaunchScope instance.
The same launcher also exposes the explicit modes used by the scripts above:

```powershell
.\start.ps1
.\start.ps1 -Mode Material
.\start.ps1 -Mode Recorded
```

Use `-Bootstrap` to refresh dependencies and `-NoBrowser` to skip opening the
login page. `Live` fails closed when its required local credentials or authorized
case are missing; use `Material` to execute only against private uploaded material.

For an OpenAI-compatible model gateway, configure only the untracked local file:

```text
LAUNCHSCOPE_MODEL_UPSTREAM_BASE_URL=https://provider.example/v1
LAUNCHSCOPE_MODEL_UPSTREAM_API_KEY=...
AGENTTEAMS_MODEL_ID=...
```

Only the local model egress service receives this key. AgentTeams Workers receive
short-lived delivery capabilities and cannot address the provider directly. The
browser never receives either credential. Free-text extraction requires an explicit
checkbox before the material is sent to the configured provider.

Open `http://127.0.0.1:3000/demo-login`. A nickname creates a random local Demo
tenant/workspace. The browser stores only the versioned Demo identifiers; it
never stores provider or infrastructure credentials. To show only the labelled
fallback without external services:

```powershell
pwsh -File scripts/demo-start.ps1 -RecordedOnly
```

Stop preserves all databases, Evidence and configuration. Reset is deliberately
restricted to `LAUNCHSCOPE_ENV=local-demo`, refuses unsafe Run/message states,
and requires `-Force`:

```powershell
pwsh -File scripts/demo-stop.ps1
pwsh -File scripts/demo-reset.ps1 -Force
```

The live route is documented in `docs/demo/5-minute-route.md`; operational and
truth-boundary details are in `docs/demo/runbook.md` and
`docs/demo/acceptance.md`. The latest local proof and external acceptance
boundary are recorded in `docs/demo/implementation-verification.md`.

## Verification

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy .
pnpm.cmd -r --if-present typecheck
pnpm.cmd -r --if-present test
docker compose -f infra/compose/docker-compose.yml config
pwsh -File scripts/verify-v01.ps1 -Environment Test -BudgetLimit 0
```

The recorded snapshot is explicitly not a claim of live AgentTeams/Matrix,
browser/search or paid-model E2E. Live acceptance requires an authorized case,
known provider usage and sanitized Run-linked evidence; otherwise status is
`BLOCKED_NO_AUTHORIZED_CASE`.
