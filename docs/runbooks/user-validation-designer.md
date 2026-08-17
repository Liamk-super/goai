# User Validation Designer runbook

## Scope

This runbook operates the ADR 0007/0008 path from the user Agent to the evidence-calibration Agent. It does not change the
current 1+5 topology, contact real users, submit paid model calls, or authorize external data collection.

## Activation

The path is registered but disabled by default. Before enabling `1.0.5`, query every tenant and confirm that no
`user-validation-designer@1.0.4` execution has status `AWAITING_STEP` or `NEEDS_ATTENTION`. Any match blocks cutover.
Configure the untracked `.env.demo.local` only after that drain gate passes:

```powershell
.venv\Scripts\python.exe scripts/check-user-validation-cutover.py
```

The command must exit zero. `demo-preflight.ps1` runs the same global database gate whenever the feature flag is true.

```text
LAUNCHSCOPE_USER_VALIDATION_ENABLED=true
LAUNCHSCOPE_USER_COPAW_MAX_ITERS=32
LAUNCHSCOPE_NODE_EXECUTABLE=node
```

Run the normal preflight and start scripts. Package validation must show one Team, six Workers and one Human both
before and after activation:

```powershell
.venv\Scripts\python.exe scripts/build-agentteams-packages.py --check
$env:LAUNCHSCOPE_USER_VALIDATION_ENABLED='true'
.venv\Scripts\python.exe scripts/build-agentteams-packages.py --check
```

## Required product inputs

Before dispatch, confirm the product profile including `one_line_value_claim`, then freeze a Product Validation Script
with one to five tasks. Each task must include a stable key, the user action, an observable expected outcome and an
optional maximum step count. The script hash is copied into RunManifest V3 for UVD-enabled Runs. Disabled Runs retain
the existing RunManifest V2 behavior.

Real user evidence is accepted only as aggregate, non-PII metadata plus a tenant-private object reference and SHA-256.
Registration does not contact, recruit, message or pay a user.

## Runtime checks

1. The user task identity is generation V3 and its exact MCP allowlist contains the UVD start, submit-step and resume
   tools. Other specialist identities cannot call them.
2. The user Worker receives `user-validation-designer@1.0.5`, uses at most 32 iterations and has a 1200-second task
   deadline. The deterministic Node Runner has no model, database, object-store or network credentials.
3. Each accepted step commits an immutable checkpoint. A process restart resumes from the next step; a changed
   checkpoint hash is rejected.
4. The complete machine result, summary/full Markdown, and summary/full HTML are stored in one tenant-private,
   content-addressed JSON object. Matrix receives AgentHandoff V2 containing only the report reference, SHA-256, mode
   and evidence references; it never transports the full report.
5. The evidence auditor reads a bounded audit slice through `user-validation-audit-context.get.v1` and records an
   AuditResult V2 decision, KB rule IDs, evidence IDs, score components and flags.
6. The Run page reads summary HTML through the authorized report endpoint. The full report opens separately. Every
   report read rechecks object metadata, the database artifact digest, and the selected content digest.

## Failure safety

- `IDEMPOTENCY_CONFLICT`: stop and compare the request body with the original operation. Never choose a new key to
  bypass the conflict.
- `SUBMISSION_UNKNOWN` or an unknown object write: the Run enters `NEEDS_ATTENTION`. Do not resubmit the model step,
  switch models or recreate the Run.
- A local deterministic Runner outage returns retryable `DEPENDENCY_UNAVAILABLE`; retry the same tool request and
  exact output. It is not a model-submission ambiguity and must not trigger another model generation.
- Checkpoint, artifact, alias, or presentation hash mismatch: reject the artifact and preserve it for audit. Do not
  regenerate a report or continue from it.
- PII, missing required input or a cross-tenant reference: block the request without echoing the prohibited value.
- Without applicable E3+ evidence, user-value conclusions remain at most `medium` and `preliminary`.

## Recheck

An evidence recheck is an append-only child Run created from an explicitly selected completed `1.0.3`, `1.0.4`, or
`1.0.5` baseline. It inherits the
already audited non-user dimensions and re-runs only user evidence interpretation, calibration and synthesis. It never
overwrites or deletes the baseline report, evidence or audit records.

## Rollout and rollback

Promote in this order: disabled registration, recorded fixture, PostgreSQL/object-store integration, local AgentTeams,
then an authorized real case. If no authorized case exists, the acceptance status is
`BLOCKED_NO_AUTHORIZED_CASE`; recorded fixtures are not external E2E proof.

Rollback first by setting `LAUNCHSCOPE_USER_VALIDATION_ENABLED=false` and restarting the local services. If any
`1.0.5` execution remains in `AWAITING_STEP` or `NEEDS_ATTENTION`, do not roll back Runner code and do not retry or
rewrite the execution. New Runs return to the legacy user task while V2/V3 reports, both Skill Manifests, evidence and
audit records remain readable. Do not delete persisted records or objects.
