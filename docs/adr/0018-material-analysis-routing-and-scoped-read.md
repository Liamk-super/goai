# ADR 0018: Material analysis, task routing, and scoped reading

- Status: Accepted
- Date: 2026-08-13
- Scope: material intake and Supervisor 1+4 generation
- Supersedes: none

## Context

LaunchScope currently stores validated source material and can attach one derived PDF analysis artifact to Agent
context. Non-PDF files are not parsed consistently, page coverage is not a durable business fact, and every Agent sees
the same compact material context. Giving Workers arbitrary object-store access would break tenant isolation and make
evidence provenance unauditable.

The product needs one reusable analysis of every supported upload, an explicit user decision over partial or failed
coverage, task-specific material routing, and a bounded way for an Agent to read only the immutable units assigned to
its Task. PostgreSQL remains the business-state authority; private object storage retains source and derived bodies;
Matrix and RocketMQ carry only commands, receipts, capability tokens, and content references.

## Decision

1. Introduce immutable `MaterialManifestV1`, `MaterialUnitV1`, and `MaterialSelectionV1` contracts. PostgreSQL records
   analysis lifecycle, unit metadata, user selection, Task scope, and read receipts; object storage retains bodies.
2. A validated upload creates a durable analysis request. Local deterministic extraction may be retried once. Model
   vision is consent-gated and any unknown submission, usage, or billing state fails closed without automatic retry.
3. A ProductProfile can be confirmed only after every material has a terminal analysis state and every partial, failed,
   or consent-blocked material has an explicit user decision. The frozen selection snapshot becomes part of the
   RequirementBrief raw input.
4. Add generation v5 contracts. `ManagerPlanV2` assigns hierarchical Material Units to Tasks, `AgentTaskTicketV4`
   freezes the assignment, and `RunManifestV5` freezes the new contracts, Agent identities, and tools.
5. Add `launchscope-context.get.v2` and `material.read.v1`. The latter accepts only Unit refs already present in the
   current Task scope, verifies every content hash, returns at most 64 KiB, and writes an immutable read receipt.
6. Existing runs and published contracts remain unchanged. The feature flag
   `LAUNCHSCOPE_MATERIAL_ROUTING_V2_ENABLED` selects the new path only for new runs. Expand-Migrate-Contract is used
   throughout; no historical run is reparsed or rewritten automatically.

## Consequences

### Positive

- Parsing cost and interpretation are reused across Agents while every claim remains traceable to a source locator.
- Failed coverage is visible and cannot be silently treated as understood.
- Workers never receive arbitrary object keys or broad project-file access.
- Old RunManifest and Agent generations remain replayable.

### Negative

- Intake gains asynchronous state, additional storage, migrations, and operational monitoring.
- Large documents require hierarchical units and bounded summaries rather than lossless prompt inclusion.
- New dependencies and a material-analysis consumer must be included in every supported startup path.

## Alternatives considered

- Send every original file to every Agent: rejected because it duplicates parsing, expands data exposure, and produces
  inconsistent interpretations.
- Route each whole file to one Agent: rejected because one document commonly spans user, product, and investment
  domains.
- Prohibit deep reading: rejected because summaries cannot safely preserve all tables, scans, and low-confidence pages.

## Failure and rollback

- Integrity mismatch, cross-tenant reference, or scope expansion is terminal and non-retryable.
- Disabling the feature flag returns new intake to generation v4; v5 runs already created retain their frozen manifest.
- Database changes are additive. Rollback stops new v5 creation but does not delete analysis, selection, scope, or receipt
  records.

