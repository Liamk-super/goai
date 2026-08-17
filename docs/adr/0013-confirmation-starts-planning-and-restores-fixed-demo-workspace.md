# ADR 0013: Final confirmation starts planning and Demo restores one fixed workspace

- Status: Accepted
- Date: 2026-08-12

## Context

The product intake flow already confirms a Product Profile and freezes a Product Validation Script before creating a Run. Requiring the user to restate the same facts in a supervisor composer created a second, contradictory intake boundary and left valid Runs parked in `PLANNED`. The local Demo also created a new tenant whenever browser session storage was absent, which hid prior PostgreSQL history and fragmented the demonstration workspace.

## Decision

The final “confirm and start evaluation” action is the explicit authorization to start supervisor planning.

For generation-v4 Runs, dispatch deterministically creates a planning-ready `RequirementBriefV1` when one does not already exist. It reads the latest confirmed Product Profile and Product Validation Script, requires all six confirmed profile fields, uses `FULL_POTENTIAL`, copies the confirmed validation goal, assigns confidence `1.0` only to explicit confirmed facts, and derives success criteria from observable task outcomes. The canonical profile and script snapshot is SHA-256 bound and written to private object storage. No Intake Model is called. An existing ready Brief is reused; a pending Brief, missing input, hash mismatch, or object-store failure remains fail-closed before a manager task is created. A pre-marker `PLANNED` breakpoint is promoted to the supervisor generation only after these durable inputs pass validation and the Brief commits.

The Run page may replay dispatch once with a stable idempotency key when a generation-v4 Run is durably `PLANNED`. The initial supervisor panel presents planning state or genuine Agent clarification requests. Supplemental requirement input remains available only behind a user-opened disclosure.

Local Demo mode restores an operator-provided binding from `.demo/default-workspace.json`. `GET /api/v1/demo/default-session` is registered only in Demo mode, requires an allowed loopback Origin, verifies the existing workspace membership, and returns the existing session without writing identity or business data. Missing or invalid bindings fail explicitly; the normal browser flow never falls back to tenant creation. The existing session-creation endpoint remains for explicit maintenance and tests.

## Consequences

- Product confirmation and supervisor intake have one authoritative boundary.
- Replays do not duplicate Briefs, Manifests, budgets, tasks, or outbox messages.
- The Demo opens on the persistent PostgreSQL workspace and keeps historical Runs visible.
- Startup now depends on a valid local binding file but does not create or migrate a workspace.
- Published contracts, frozen contract tests, and the database schema remain unchanged.
