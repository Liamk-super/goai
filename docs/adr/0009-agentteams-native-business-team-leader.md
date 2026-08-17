# ADR 0009: AgentTeams native business Team Leader

- Status: Accepted
- Date: 2026-08-11
- Decision owners: LaunchScope architecture and evaluation control plane

## Context

LaunchScope has a fixed physical 1+5 Agent topology: one evaluation manager, four domain reviewers, and one independent evidence auditor. The earlier runtime dispatched a static DAG and constrained the evaluation manager to return an empty gate handoff. That preserved control-plane authority but did not exercise AgentTeams' native Team Leader collaboration model.

The competition demonstration requires visible, authentic AgentTeams collaboration without allowing Matrix messages or Agents to become a second business-state authority.

## Decision

The AgentTeams global Manager remains the platform router. `evaluation-manager` is the LaunchScope business Team Leader. Product-facing language is “主管 + 四域 Agent + 独立证据校准”; the physical topology remains 1+5.

The Team Leader may cognitively decompose an evaluation, propose a bounded plan, delegate approved Tasks, coordinate conflicts and progress, and propose a synthesis. The PostgreSQL-backed control plane alone authenticates identities, approves plans, enforces permissions and budgets, materializes Tasks, commits state, handles idempotency and approvals, and writes the final report.

The four domain Agents remain parallel reviewers. `evidence-auditor` remains an independent calibration principal and is never merged into the Team Leader. The Team Leader cannot manufacture specialist conclusions or override audit outcomes.

AgentTeams Project state, the Team Room, Worker Rooms, and Matrix events are collaboration projections of durable PostgreSQL state. PostgreSQL wins every conflict. Free text and ordinary chat never change Run, Task, report, budget, approval, or audit state.

The native flow uses the official AgentTeams rooms:

- the initial planning Task is delivered to `leaderDMRoomID`;
- non-sensitive plan and progress summaries are posted to `teamRoomID`;
- full, capability-bearing Task tickets are delivered only to the target Worker's official `roomID`.

LaunchScope does not create replacement Human-to-Agent rooms. It reconciles room and Worker identities from AgentTeams resources.

The Team Leader uses AgentTeams' built-in `project-management`, `task-management`, `team-coordination`, `communication`, `file-sharing`, and `mcporter` Skills. LaunchScope packages do not copy or shadow those Skills. Creating or deleting Workers, changing models, reading secrets, expanding roles/tools/budgets, and direct business-state writes remain prohibited.

## Versioning and state transitions

This decision uses Expand-Migrate-Contract:

- `ManagerPlanV1`, `AgentTaskTicketV2`, and `ManagerSynthesisV1` are new contracts;
- Agent identity generation v4 and Run Manifest v4 are additive;
- released Agent, Handoff, Run Manifest, and contract-test artifacts remain byte-for-byte unchanged;
- migration 0019 adds plan and room-delivery state without removing legacy columns.

With `LAUNCHSCOPE_AGENTTEAMS_NATIVE_LEADER_ENABLED=false`, existing Runs keep the legacy static flow. A newly admitted native Run initially materializes only `LEADER_PLANNING`. An approved plan materializes the domain, audit, and synthesis Tasks. Plan validation rejects missing or duplicate roles, cycles, tool or budget expansion, and invalid replans.

A schema or policy error may be corrected once only when the control plane knows that no external side effect occurred. Replanning may change only unstarted Tasks. `SUBMISSION_UNKNOWN`, unknown usage or billing, and uncertain external side effects remain fail-closed and forbid retry or replan.

After independent evidence calibration, the control plane computes the deterministic recommendation and passes only the audited read-only slice to the Team Leader. The Team Leader explains cross-domain relationships, risks, conflicts, and one to three validation experiments. It cannot change deterministic grades. A different proposed recommendation creates an auditable conflict and enters the existing one-time approval flow; it is never silently substituted.

## Consequences

AgentTeams collaboration becomes visible and traceable while the business state remains deterministic and centrally governed. Additional durable plan, delegation, Matrix receipt, and synthesis records are required. Live acceptance requires an authorized external case; recorded snapshots and local contract tests cannot be presented as live AgentTeams E2E.

## Rollout and rollback

The native flow is disabled by default. Before enabling it, operators drain legacy AgentTeams Tasks in active or attention states, provision all six v4 Worker packages, and activate Run Manifest v4. Rollback first disables new native Run admission. Worker packages are not rolled back while native Runs remain in flight. Historical plans, events, approvals, and reports remain immutable and readable.
