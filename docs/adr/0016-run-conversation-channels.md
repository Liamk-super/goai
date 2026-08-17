# ADR-0016: Controlled Run conversation channels

## Status

Accepted

## Context

The generation-v4 Run page currently exposes a large, permanent Supervisor card while the legacy page owns a separate four-specialist drawer. This creates two competing interaction models and makes the prediction instrument secondary. Users need one compact conversation surface for the Supervisor and the three generation-v4 domain specialists without exposing Matrix rooms, internal prompts, or the Evidence Auditor as a user-facing chat participant.

PostgreSQL remains the business-state source of truth. A user message cannot directly rewrite a running or completed Task, and a visual chat acknowledgement cannot claim that an Agent or paid model executed. Unknown submission, usage, billing, or external side effects must remain fail closed.

## Decision

Generation-v4 Runs expose exactly four user-facing conversation channels: `supervisor`, `user-evidence`, `product-engineering`, and `business-investment`. The Evidence Auditor remains a serial control-plane participant and never becomes a user chat channel. Legacy Runs retain their existing presentation.

Run conversation messages are append-only PostgreSQL metadata with content-addressed bodies in private object storage. The API returns only user-safe messages, routing receipts, task summaries, evidence counts, and open information requests; it never returns Matrix payloads, chain-of-thought, hidden prompts, or raw internal collaboration.

The Supervisor channel reuses the existing governed intake and requirement-change boundary. A specialist-channel message is a routing hint, not a direct Worker command: the control plane records it and scopes it only to that specialist's not-yet-started generation-v4 Tasks. An existing `NEEDS_INPUT` question continues through the clarification API, which updates the durable profile and redispatches only affected Tasks. Running or completed Tasks are never silently rewritten or restarted by a free-form message.

Every write requires `Idempotency-Key` and `X-Correlation-Id`. Same-key/same-payload requests replay the original routing receipt; same-key/different-payload requests return `IDEMPOTENCY_CONFLICT`.

## Consequences

### Positive

- The prediction instrument remains the primary Run-page surface while conversations are available on demand.
- Conversation refresh and recovery are backed by durable tenant-scoped records.
- Specialist messages cannot bypass the Supervisor, task state machine, budgets, or execution controls.
- The user-facing topology matches the strict Supervisor plus three-domain-Agent generation.

### Negative

- A specialist message may remain `RECORDED` until the controlled workflow reaches relevant not-yet-started work.
- The UI must distinguish a routing receipt from an actual Agent response.
- Conversation bodies require private-object-store availability when read.

### Neutral

- The released Supervisor Chat V1 contract remains immutable; Run Conversation V1 is additive.
- Public shared Run pages remain read-only and do not expose conversation bodies or composers.

## Alternatives Considered

**Directly send user messages to Worker Matrix rooms**

Rejected because it bypasses control-plane validation, attribution, execution control, and budget admission.

**Expose the Evidence Auditor as the fourth specialist chat**

Rejected because the auditor validates immutable evidence and must not collect or reinterpret product facts from users.

**Reuse the legacy four-domain drawer unchanged**

Rejected because it includes `geo-policy-trend`, which is not a physical generation-v4 Worker.

## References

- ADR-0010: Supervisor Agent one-plus-four generation
- ADR-0012: Supervisor one-plus-four-only admission and Agent reports
- ADR-0014: Run-scoped execution pause and model egress gate

