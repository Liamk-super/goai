# LaunchScope generation-aware implementation verification

Verified locally on 2026-08-06. This record separates reproducible local proof
from external AgentTeams/provider acceptance.

## Implemented scope

- Browser-local versioned Demo identity and guarded workspace API.
- Pinned AgentTeams v1.2.0 `agentteams.io/v1beta1` bundles: the legacy Team retains six
  independent Workers, while the feature-flagged generation-v4 Team has exactly five Workers, one supervisor
  Team Leader, no `geo-policy-trend`, disabled peer mentions, and a generation-specific Human coordinator. Windows bootstrap downloads
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
| AgentTeams deterministic package/resource validation | legacy: 1 Team, 6 Workers, 1 Human; v4: 1 Team, 5 Workers, 1 Human |
| Web tests | 7 passed |
| Web and Ops typecheck/production build | passed |
| Demo PowerShell parser validation | passed |

The temporary PostgreSQL container created for this verification was matched by
exact name/image/port, stopped, and removed. Existing workspace services and
their volumes were not changed.

## External acceptance status in this historical record

`BLOCKED_NO_AUTHORIZED_CASE`

No authorized external URL, paid model/search credentials, or live AgentTeams provider case was supplied for the
2026-08-06 verification. Therefore that historical result does not claim a live AgentTeams/Matrix/RocketMQ/
browser/search/paid-model E2E. The current M7-B acceptance must follow `docs/demo/acceptance.md`; later live results
must be appended as a new dated section rather than rewriting this historical proof level.
