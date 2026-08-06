# ADR 0003: Demo identity, AgentTeams v1.2.0, and asynchronous dispatch

- Status: Accepted for LaunchScope v0.2 Demo
- Date: 2026-08-06
- Scope: local competition Demo packaging
- Supersedes: none

## Context

The frozen V1.0 architecture requires PostgreSQL business truth, AgentTeams
and Matrix collaboration, RocketMQ background delivery, durable SSE, tenant
isolation, immutable evidence, and fail-closed paid operations. The v0.1
vertical slice proved those boundaries locally but used environment-projected
browser identity and a deterministic synchronous execution endpoint.

The v0.2 Demo must show an observable official AgentTeams 1+5 team without
claiming that a local nickname is production authentication or that a recorded
acceptance snapshot is a live run.

## Decision

1. The Demo pins official AgentTeams `v1.2.0` and its final
   `agentteams.io/v1beta1` contract. It runs as an independent embedded stack,
   with its own Controller, Higress, Tuwunel, Element, and MinIO ports and
   storage. LaunchScope PostgreSQL and private evidence storage remain the
   business authority.
2. Six business roles are independent Worker resources. One and only one Team
   member is `team_leader`; the other five are Workers. The AgentTeams Manager
   is a platform router and does not count toward the business 1+5. A Human
   resource represents the LaunchScope coordinator.
3. Local Demo identity is enabled only by `LAUNCHSCOPE_DEMO_MODE=true` and a
   configured loopback Origin. The browser stores a versioned identifier
   object, never a secret. The server creates a random tenant, owner actor, and
   workspace, and validates membership on every workspace entry. Production
   does not register these routes.
4. Run dispatch commits the frozen manifest, tasks, budget reservation, and
   transactional Outbox message before returning `202`. RocketMQ and Matrix
   are transports only. Consumers use Inbox and Matrix event identifiers for
   idempotency; every progress projection is committed before SSE exposure.
5. Unknown submission, tool side effect, usage, or billing transitions the Run
   to `NEEDS_ATTENTION`. No automatic retry, model/runtime switch,
   resubmission, failover, manual settlement, or raw-SQL repair is allowed.
6. Recorded V1/V2 acceptance snapshots are immutable, redacted, hash-verified,
   and visibly labelled. They never satisfy a live external E2E gate.

## Consequences

- The Demo can be reset without weakening production identity or RLS policy.
- AgentTeams collaboration is inspectable while business state remains
  reconstructable from PostgreSQL by Run ID.
- Missing authorized URLs or model/search credentials blocks only the real
  external acceptance gate; it does not authorize fabricated success.

## Verification

- API tests cover disabled routes, loopback Origin policy, nickname validation,
  session recovery, and tenant/workspace boundaries.
- AgentTeams contract tests require v1.2.0, six Workers, exactly one Leader,
  one Team, one Human, role-specific packages, and identity/permission parity.
- Messaging, Matrix, evidence, budget, scripts, and Web E2E tests are required
  before the v0.2 Demo may be labelled live-ready.
