# LaunchScope v0.2 implementation verification

Verified locally on 2026-08-06. This record separates reproducible local proof
from external AgentTeams/provider acceptance.

## Implemented scope

- Browser-local versioned Demo identity and guarded workspace API.
- Pinned AgentTeams v1.2.0 `agentteams.io/v1beta1` bundle: one Team, six
  independent Workers, and one Human coordinator. Windows bootstrap downloads
  the tagged official PowerShell installer and verifies SHA-256 before optional
  execution.
- RocketMQ 5.x Proxy dispatch using the official Python client, transactional
  Outbox/Inbox, least-privilege publisher role, Matrix idempotent bridge and
  durable SSE stage history.
- Official MCP Python SDK 2.0 Streamable HTTP servers for context, browser audit
  and public search. Each capability has a separate endpoint, bearer consumer
  authentication, tenant/run/task routing, allowlists, quotas and private
  Evidence persistence.
- Strict `AgentHandoffV1`, provider-usage reconciliation, USD 20 hard limit,
  four-domain gate, independent per-Finding audit and deterministic rule-owned
  synthesis.
- Live run/report/compare UI, separately rooted read-only recorded snapshot,
  bounded acceptance exporter, bootstrap/preflight/start/stop/reset scripts and
  five-minute route.

## Reproducible proof

| Check | Result |
|---|---|
| Python unit/contract/API suite | 103 passed; external/database opt-in cases skipped |
| PostgreSQL integration and local E2E | 39 passed; MinIO case excluded from this invocation |
| Real local MinIO presigned PUT, HEAD, signed read, anonymous denial | 1 passed |
| Ruff | passed across `apps`, `packages`, `scripts`, and `tests` |
| mypy | passed for 69 API/orchestrator source files |
| AgentTeams deterministic package/resource validation | 1 Team, 6 Workers, 1 Human |
| Web tests | 7 passed |
| Web and Ops typecheck/production build | passed |
| Demo PowerShell parser validation | passed |

The temporary PostgreSQL container created for this verification was matched by
exact name/image/port, stopped, and removed. Existing workspace services and
their volumes were not changed.

## External acceptance status

`BLOCKED_NO_AUTHORIZED_CASE`

No authorized external URL, paid model/search credentials, or live AgentTeams
provider case was supplied. Therefore this verification does not claim a live
AgentTeams/Matrix/RocketMQ/browser/search/paid-model E2E. A recorded snapshot
remains visibly labelled and read-only. Live completion requires the sanitized
Run-linked facts listed in `docs/demo/acceptance.md`, including provider usage,
Matrix receipts, fully published Outbox rows and real allowlisted Evidence.

