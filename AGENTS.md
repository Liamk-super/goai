# AGENTS.md

Project context for AI agents working in the LaunchScope / 势能引擎 repository.

## Project overview

LaunchScope is an evidence-driven product validation control plane. It orchestrates
AgentTeams (1+5 topology) via RocketMQ, uses Matrix for collaboration, PostgreSQL as
the business-state source of truth, and exposes REST/SSE control-plane interfaces.
The live demo route and product intent are documented in `docs/demo/` and `README.md`.

## Repository layout

- `apps/api` — REST/SSE control-plane implementation (Python)
- `apps/orchestrator` — orchestration service (Python)
- `apps/worker` — background worker (Python)
- `apps/web` — browser UI (Next.js/TypeScript)
- `apps/ops` — ops UI (Next.js/TypeScript)
- `packages/domain` — domain services (Python)
- `packages/contracts` — frozen JSON Schema / OpenAPI contracts + contract tests (Python)
- `packages/skills` — versioned Skill definitions (Python)
- `packages/observability` — observability helpers (Python)
- `infra/` — Docker Compose, RocketMQ, Nacos, Polardb, Higress, AgentTeams, observability configs
- `scripts/` — PowerShell bootstrap/preflight/verify scripts
- `docs/` — ADRs, plans, runbooks, demo docs, architecture baseline
- `tests/` — workspace-level tests

## Frozen boundaries (most important rules)

- `docs/势能引擎技术架构基线_V1.0.md` and everything under `reference/` are read-only inputs.
  Never edit them without a separately authorized decision (see ADR 0001).
- Published contract files under `packages/contracts/` are immutable. Breaking changes require a
  new major contract file plus an ADR; use Expand-Migrate-Contract. Never edit a released schema in place.
- Contract tests in `packages/contracts/tests/` are frozen artifacts — keep their source text unchanged.
- Any request that changes a frozen boundary must stop and write an ADR before editing a contract.
- T1 does not authorize Git staging, commits, pushes, deployment, or external calls.

## Architecture invariants

- PostgreSQL is the only business-state source of truth. Control plane validates and commits
  state, approval, budget, idempotency and audit; consumers (Matrix, RocketMQ, Agents, Workers)
  cannot directly update business state.
- REST writes require `Idempotency-Key` and `X-Correlation-Id`. Repeated key + same hash replays
  the original result; different hash returns `IDEMPOTENCY_CONFLICT`.
- SSE is a projection of durable state, not an in-memory progress channel. `Last-Event-ID` wins
  over the query `cursor`; invalid/expired cursors return `CURSOR_INVALID`.
- Fail-closed: `SUBMISSION_UNKNOWN`, unknown billing, or uncertain external side effects are
  never auto-retried; they surface `run.needs_attention`.

## Environment & prerequisites

- Python 3.11–3.13, repository `.venv` (never the system Python)
- Node.js 20.19+, pnpm 11.9
- Docker Desktop with Compose

## Install / bootstrap

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]" -e packages/domain -e packages/contracts -e packages/skills -e packages/observability -e apps/api -e apps/orchestrator -e apps/worker
pnpm.cmd install --frozen-lockfile
pwsh -File scripts/demo-bootstrap.ps1 -InstallAgentTeams
pwsh -File scripts/demo-preflight.ps1 -RequireExternalCase
pwsh -File scripts/demo-start.ps1
```

- Use `-RecordedOnly` to run with the recorded fallback (no external services).
- `scripts/demo-stop.ps1` / `scripts/demo-reset.ps1 -Force` to stop/reset (reset only allowed in `local-demo`).
- Model gateway config goes only in the untracked `.env.demo.local`; never commit keys or Matrix tokens.

## Verification commands

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy .
pnpm.cmd -r --if-present typecheck
pnpm.cmd -r --if-present test
docker compose -f infra/compose/docker-compose.yml config
pwsh -File scripts/verify-v01.ps1 -Environment Test -BudgetLimit 0
```

## Startup compatibility

- Any change to Alembic migrations, environment variables, Docker/Compose ports, dependencies,
  service names, API/Web entrypoints, or process launch behavior must also inspect and update
  `start.ps1`, `start.cmd`, and the affected `scripts/demo-*.ps1` path in the same task.
- Alembic revision identifiers must be at most 32 characters because the existing
  `alembic_version.version_num` column is `varchar(32)`.
- Before handing off a startup-affecting change, run the focused script/migration tests and
  perform a real `./start.ps1 -Mode Recorded -NoBrowser` smoke test, then verify Web, Ops, and
  API HTTP responses. Static checks alone are not sufficient.
- Preserve the existing PostgreSQL volume and browser Demo workspace. Never use reset/clean or
  create a replacement tenant to make startup pass.

## Code style

- Python: ruff rules `E F I UP B SIM`, line length 120, target py311.
- TypeScript: prettier + tsc strict via workspace `typecheck`.
- Do not add comments to code unless asked.
- Match existing conventions; prefer existing patterns over introducing new libraries.

## Skills & MCP usage

- Architecture decisions / ADR / system design → `architecture-designer`
- Code review (working tree or PR) → `code-reviewer`; frontend files → `frontend-code-review`
- Debugging / root-cause analysis → `debug-pro`
- Security review → `security-auditor`
- Tests (write/run/fix) → `test-runner`; TDD → `tdd`
- Frontend UI work in `apps/web` / `apps/ops` → `frontend-design`
- No MCP servers are required for normal development; interact with remote services only via the documented scripts.

## Documentation

- `docs/adr/` — architecture decision records (read before changing frozen boundaries)
- `docs/plans/` — implementation plans
- `docs/runbooks/` — operational runbooks (retention/delete, unknown submission, demo)
- `docs/demo/` — live route, acceptance, runbook, implementation verification
- `docs/技术验收评估报告_V1.0.md` — technical acceptance assessment

## Notes

- The recorded snapshot is not a claim of live AgentTeams/Matrix, browser/search or paid-model E2E.
  Live acceptance requires an authorized case; otherwise status is `BLOCKED_NO_AUTHORIZED_CASE`.
- The repository is a monorepo with pnpm workspaces for JS and editable-installed Python packages.
