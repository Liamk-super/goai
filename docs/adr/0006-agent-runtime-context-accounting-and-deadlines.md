# ADR 0006: Agent runtime context, accounting, and deadlines

- Status: Accepted
- Date: 2026-08-09

## Context

The authorized product URL existed only in process configuration, while an Agent Task received a product-profile projection that could omit it. Successful browser/search executions persisted Evidence but did not write the existing Skill/Tool invocation ledger. AgentTeams CoPaw v1.2 reports model usage as cumulative counters per dedicated Worker, not as a Matrix Task receipt. Matrix delivery also left the durable Task at `READY`, so its 600-second timeout had no durable start or convergence path.

## Decision

1. Dispatch freezes validated `authorized_urls` into `run_manifest.frozen_config.research_targets`. The context tool and Task research policy expose the same frozen list. Agents may browser-audit only these URLs and must not guess domains. This is an additive response field under the existing open `output_schema`; no published Contract is edited.
2. Every dispatched Task resolves an immutable `skill_version_id`. A successful Evidence write records its `skill_invocation` and `tool_invocation` in the same PostgreSQL transaction, with a canonical parameter hash and no credential material.
3. A successful Matrix assignment creates one durable `agentteams_task_delivery` row for `(Task, dispatch_epoch)`, changes `READY` to `RUNNING`, and fixes `delivered_at` plus `deadline_at`. A control-plane watchdog changes expired `RUNNING` Tasks and Runs to `NEEDS_ATTENTION/TIMEOUT` without retrying them.
4. The local AgentTeams integration samples CoPaw's cumulative token counters immediately before Matrix delivery and after the terminal handoff. LaunchScope Workers are dedicated per Agent role; delivery is serialized by their durable Task lifecycle. The non-negative delta becomes the Task receipt and is priced with the Run's frozen rates. Missing, reset, or zero-call counters are usage-unknown when receipts are required.

## Consequences

- Authorized targets, real tool use, Task deadlines, token quantity, and model cost are now queryable from PostgreSQL for each Run.
- Model accounting depends on dedicated LaunchScope Workers. Sharing a Worker with unrelated chats would make a counter delta unattributable and is not an accepted deployment topology.
- The watchdog is fail-closed: timeout and telemetry ambiguity surface for attention and never trigger automatic resubmission.
- The migration is additive. Released JSON Schemas and frozen Contract tests remain unchanged.

## Alternatives considered

- Copy the URL only into Agent prompt text: rejected because it is not a durable Run input.
- Estimate tokens from text length: rejected because it would look like provider usage without provider counters.
- Extend timeout values without a delivery receipt: rejected because it would retain the missing terminal-state transition.
