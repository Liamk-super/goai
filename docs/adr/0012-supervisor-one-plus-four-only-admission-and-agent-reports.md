# ADR 0012: Generation-v4-only admission and private Agent report access

- Status: Accepted
- Date: 2026-08-12
- Decision owners: LaunchScope product and evaluation control plane
- Amends: ADR 0010 feature-flag admission and rollback behavior

## Context

ADR 0010 introduced the physical supervisor 1+4 generation behind a default-off flag while retaining legacy 1+5
admission as a rollback path. The product now adopts 1+4 as the only topology for new evaluations. It also needs a
continuous evaluation-wheel experience and user-initiated access to the three domain reports plus the independent
auditor output without making those internal reports compete with the supervisor's final report.

## Decision

New evaluation dispatch is generation-v4-only. `LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED=true` admits the supervisor,
user, product, investment, and auditor bundle. When the flag is false, new dispatch fails closed with
`SUPERVISOR_1P4_DISABLED`; it never materializes the legacy topology. Frozen legacy Runs and their reports remain
readable indefinitely, and an in-flight Run never changes generation.

Each accepted generation-v4 domain handoff registers its existing immutable `report_ref` in an append-only Agent
report catalog. Each accepted auditor round is canonically serialized to a private immutable object and registered
in the same catalog. PostgreSQL owns catalog visibility and status; object storage owns bodies bound by SHA-256.
Matrix messages are transport only.

The supervisor report remains the default product conclusion. Agent report bodies are fetched only after an
authorized user explicitly opens the collapsed process surface. Missing or failed Agents remain explicit and are
never replaced with synthetic reports. Unknown report persistence or integrity enters `NEEDS_ATTENTION` and is not
automatically retried.

The evaluation wheel appears throughout pre-report product routes. During `RUNNING`, a decorative ring may rotate
slowly while durable evidence alone advances the ratchet. Waiting and attention states stop motion. The final report
route contains no wheel.

## Consequences

- Rollback closes new admission instead of reviving 1+5.
- Historical readers remain generation-aware and unchanged.
- Agent report access adds an append-only tenant-scoped table and additive read-only API; released contracts remain
  untouched.
- Local and Recorded verification does not prove live AgentTeams, model, tool, usage, or billing closure.
