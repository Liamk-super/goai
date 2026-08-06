# LaunchScope V0.1 final local gates

Environment: local PostgreSQL/RLS, disposable MinIO, FastAPI, Next.js Web and Ops. No paid or external provider call was made.

| Gate | Result |
|---|---|
| `.venv\Scripts\python.exe -m pytest -q` with PostgreSQL and live MinIO | PASS: 108 passed, 1 external-case skip |
| `.venv\Scripts\python.exe -m ruff check .` | PASS |
| `.venv\Scripts\python.exe -m mypy .` | PASS: 97 runtime source files |
| `pnpm.cmd install --frozen-lockfile` | PASS: lockfile unchanged |
| `pnpm.cmd -r typecheck` | PASS: Web and Ops |
| `pnpm.cmd -r test` | PASS: Web 3 passed; Ops has no unit files |
| `pnpm.cmd --filter web build` | PASS: 10 routes |
| `pnpm.cmd --filter ops build` | PASS: 4 routes |
| `alembic upgrade head` and `alembic current` | PASS: `0012_t12_seed_p0_skills (head)` |
| base/test `docker compose config --quiet` plus policy script | PASS |
| repository secret-pattern scan excluding secure local env/artifacts | PASS |
| `scripts/verify-v01.ps1 -Environment Test -BudgetLimit 0` | PASS; external readonly case explicitly BLOCKED |
| Playwright CLI local browser walkthrough | PASS: project, direct upload, questions, confirm, plan, execution, SSE, evidence, report, compare, Ops and 404 boundary |

Known non-blocking warning: the installed FastAPI TestClient compatibility layer emits one upstream Starlette deprecation warning. The suite has no failing warning or test.

External public-research/AgentTeams/Matrix/LLM/provider E2E remains `BLOCKED_NO_AUTHORIZED_CASE`; local deterministic evidence is not presented as that external proof.
