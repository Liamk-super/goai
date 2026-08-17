# ADR-0021: Expand domain handoff recovery epochs

## Status

Accepted

## Context

Report v2.2 domain work can require more than one operational recovery when a long model stream disconnects or an
upstream service rejects a delivery. PostgreSQL correctly increments `task.dispatch_epoch` for each recovered
delivery, but the published `agent-handoff.v3.json` contract permits only epochs 0 and 1. A second recovery therefore
cannot produce a document that both matches durable task routing and validates against v3. Editing the published v3
schema or its frozen tests is prohibited.

The CoPaw Worker iteration limit can also be configured above a Run's original model-call allowance. If delivery
accounting keeps the smaller value, the gateway can stop an otherwise healthy Agent before the Worker reaches its
configured iteration limit.

## Decision

Add `agent-handoff.v4.json` as an additive contract. It preserves v3 finding, evidence, status, and immutable report
semantics, changes `schema_version` to `4.0`, and permits any non-negative epoch representable by PostgreSQL's integer
column. Generation v6 continues to use v3 for epochs 0 and 1; a recovered domain or remediation delivery with epoch
greater than 1 explicitly negotiates `AgentHandoffV4` in its Matrix assignment. Consumers accept both versions and
validate the embedded document against the matching schema.

Delivery accounting uses the greater of the Run-frozen iteration allowance and the configured CoPaw Worker limit.
This is an operational capacity guard only; it does not change scoring, evidence admission, task routing, or report
content.

## Consequences

### Positive

- Existing v3 producers, consumers, fixtures, and frozen tests remain unchanged.
- Recovered deliveries retain exact PostgreSQL epoch routing without direct state repair.
- The model gateway no longer cuts off a Worker earlier than its configured iteration budget.

### Negative

- Generation v6 consumers must understand two domain handoff message types during migration.
- A recovered delivery can use a newer transport contract than its original immutable planning ticket.

### Neutral

- Initial and first-remediation domain deliveries remain byte-compatible with v3.
- Task timeout, evidence policy, scoring, and immutable report persistence are unaffected.

## Alternatives Considered

Editing v3 was rejected because published contracts are immutable. Capping or rewriting the PostgreSQL epoch was
rejected because it would destroy delivery identity and idempotency. Creating a replacement Run was rejected because
the formal evaluation must retain one durable Run and recover in place.

## References

- ADR-0017 delivery-scoped model ledger
- ADR-0019 demo force Run recovery
- ADR-0020 report v2.2 baseline, citations, and public Demo
