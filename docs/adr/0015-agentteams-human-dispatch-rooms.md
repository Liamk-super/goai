# ADR-0015: Stable Human dispatch rooms for AgentTeams assignments

## Status

Accepted

## Context

ADR-0009 routes capability-bearing assignments to AgentTeams' internal Worker rooms. In AgentTeams v1.2.0 those rooms belong to the Manager-to-Worker collaboration plane. The registered Human resource can join its authorized Team Room, but it cannot read membership or send messages in `leaderDMRoomID` or a Worker's `roomID`; Matrix correctly returns a forbidden response.

The previous adapter worked around that boundary by creating a new private Human-to-Worker room for every Task. Matrix accepted the room creation before the invited Worker had joined. LaunchScope then recorded the write as a durable Task delivery even though the Worker could not consume it. The Task remained `RUNNING` until its deadline, with no Agent model-usage interval or structured handoff.

PostgreSQL must remain the business-state source of truth, capability-bearing tickets must remain private to the selected Worker, and unknown external submission or usage must remain fail closed.

## Decision

The AgentTeams Manager-to-Worker rooms remain native collaboration resources and are not repurposed. LaunchScope provisions one stable Matrix direct room between the authenticated AgentTeams Human and each selected Worker. The control-plane mapping is generation-aware and is stored in the untracked runtime environment as `LAUNCHSCOPE_MATRIX_AGENT_ROOMS_JSON`.

Before every assignment write, the adapter resolves the authenticated Human with Matrix `whoami`, reads the mapped room's joined membership, and requires the room to contain exactly that Human and the target Worker's current Matrix identity. A missing mapping, stale identity, inaccessible room, unjoined Worker, or additional member prevents the write. Every assignment and stop command carries a Matrix `m.mentions.user_ids` entry for the target Worker's full MXID because AgentTeams Workers run with mention-required group channels. The dispatcher never creates a per-Task room.

The Matrix event transaction identifier remains the durable dispatch event identifier, so a repeated transport write is idempotent. A successful Matrix write proves only that the assignment entered a stable, consumable two-party room. Worker execution is proven separately by the existing structured handoff and provider usage delta; room delivery alone never proves model execution or successful completion.

This decision supersedes only ADR-0009's physical room-routing paragraph. The Team Leader role, PostgreSQL authority, capability boundaries, native Team Room projection, contracts, state transitions, billing rules, and fail-closed recovery policy are unchanged.

## Consequences

- Dispatch no longer races Worker invitation and room joining.
- AgentTeams' mention-required channel admits the assignment instead of silently ignoring an ordinary room message.
- Startup and live preflight verify every generation-selected Human-to-Worker room before admitting external execution.
- Capability-bearing tickets remain isolated to the authenticated Human and one target Worker.
- A Worker identity replacement requires reprovisioning its stable room before dispatch resumes.
- Matrix delivery, provider usage, and structured handoff remain distinct evidence layers.

## Alternatives Considered

**Send directly to `leaderDMRoomID` and Worker `roomID`**

Rejected because the authenticated Human is not a member of those Manager-owned rooms and cannot write to them.

**Send all assignments to `teamRoomID`**

Rejected because every Team member could observe another Worker's capability-bearing ticket.

**Create one private room per Task**

Rejected because room creation does not prove that the invited Worker has joined, which caused the observed delivered-without-consumer timeout.

**Treat a Matrix write as execution acknowledgement**

Rejected because it proves server acceptance only. Execution remains attributable through provider usage and the terminal structured handoff.

## References

- ADR-0006: Agent runtime context, accounting, and deadlines
- ADR-0009: AgentTeams native business Team Leader
- ADR-0014: Run-scoped execution pause and model egress gate
