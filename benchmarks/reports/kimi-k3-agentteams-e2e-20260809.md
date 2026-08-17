# Kimi K3 AgentTeams E2E evidence — 2026-08-09

## Acceptance result

- Authorized case: the same confirmed ProductVersion `1f151d1c-dc0a-481f-ae47-0663f4e66600` used by the prior run.
- Candidate run: `6921d3dc-758d-401a-9c08-6930ef50169d`.
- Runtime identity: all six Running AgentTeams Worker resources reported exactly `kimi-k3` immediately before dispatch.
- Canonical terminal control state: `NEEDS_ATTENTION`.
- Failure reason: `search submission or billing state is unknown`.
- Acceptance: **NOT PASSED**. No retry, failover, replacement submission, model switch, manual settlement or state edit was performed after the unknown search state.

## Runtime-condition correction

- The browser quota remains two calls per Task and search remains eight queries per Task.
- The frozen Run Manifest and every Agent assignment now carry those exact limits.
- CoPaw `max_iters` was raised from 12 to 30 for the six dedicated Workers.
- Cross-session CoPaw memory summary and memory prompt were disabled so a Task cannot reuse an earlier assignment's context token.
- Benchmark Cases, Oracles and scoring rules were unchanged.

## Observed real path

| Component | Durable evidence | Result |
|---|---|---|
| Leader planning | Leader Task reached `SUCCEEDED` | EXECUTED |
| Product engineering | two `browser-audit.v1` invocations, both `SUCCEEDED`, two Evidence rows | EXECUTED |
| User evidence | one `browser-audit.v1` invocation `SUCCEEDED`, one Evidence row | EXECUTED, handoff not completed |
| Geo/policy/trend | one `public-research-search.v1` invocation `SUCCEEDED`, one Evidence row | EXECUTED, later search state unknown |
| Business/investment | specialist Task entered `RUNNING` | STARTED, no durable Evidence before stop |
| Evidence calibration | Auditor remained dependency-blocked | NOT EXECUTED |
| Final synthesis | synthesis remained dependency-blocked | NOT EXECUTED |

The run therefore proves real Kimi K3 Leader, specialist, browser and search execution after the quota fix, but it does
not prove evidence calibration, final synthesis or a normal terminal 1+5 E2E. The body-free database export is stored
locally at `artifacts/benchmarks/kimi-k3-e2e-6921d3dc/`.

## Related recovery attempts

- `fbb1b40c-244e-48b9-b573-dc969eec9928`: contaminated by an in-progress Worker package rebuild; stopped at Leader.
- `5ae10d62-1910-4d45-8371-363424d384ac`: exposed `ITERATION_LIMIT_BEFORE_SEARCH` and stale context-token injection; no clean System E2E claim.
- `6921d3dc-758d-401a-9c08-6930ef50169d`: stable Workers and corrected runtime conditions, then fail-closed on unknown search submission/billing.
