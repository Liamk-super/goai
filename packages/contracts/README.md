# LaunchScope contracts

This directory contains the T1 frozen contracts for the product-agnostic
LaunchScope control plane. It is a contract package, not a business
implementation. No Demo product is selected here, and no model, browser,
search, Matrix, RocketMQ, paid service, production system, or credential is
used by these files.

## Authority and scope

`docs/势能引擎技术架构基线_V1.0.md` is the only architecture source of truth.
The implementation plan supplies T1 work sequencing. The materials under
`reference/` are read-only context and cannot override the baseline. T1 does
not create applications, database migrations, workers, adapters, infrastructure
or business flows.

The frozen entry decisions are recorded in:

- `docs/adr/0001-frozen-boundaries-and-change-policy.md`
- `docs/adr/0002-p0-skill-set-reconciliation.md`

## Contract map

| Contract | Purpose | Version authority |
|---|---|---|
| `events/envelope.schema.json` | Shared event/command transport fields | `schema_version` in every message |
| `events/evaluation-events.v1.json` | Evaluation lifecycle event union and payloads | `x-contract-version: 1.0` |
| `commands/run-commands.v1.json` | Run command union and payloads | `x-contract-version: 1.0` |
| `openapi/control-plane.v1.yaml` | REST, cursor pages, errors and SSE resume semantics | OpenAPI `info.version` plus `x-contract-version` |
| `unified-model/launchscope-unified-model.v1.json` | Semantic exchange snapshot, Agent Identity and Skill descriptors | `schema_version: 1.0` |

## Message envelope

Every event and command contains these required fields:

| Field | Meaning |
|---|---|
| `event_id` | Unique message identity. The frozen name is retained for commands too. |
| `tenant_id` | Tenant isolation scope. |
| `run_id` | Evaluation run scope. |
| `task_id` | Task scope, or `null` for run-level messages. |
| `correlation_id` | Trace and request chain identity. |
| `causation_id` | Parent message identity, or `null` for a root message. |
| `idempotency_key` | Caller/message deduplication key. A duplicate must not cause a second state change or side effect. |
| `schema_version` | Independent message contract version in `MAJOR.MINOR` form. |
| `occurred_at` | RFC 3339 timestamp supplied by the producer. |
| `payload` | Structured event/command data; never a full chat transcript or private chain of thought. |

Events additionally require `event_type`; commands require `command_type`.
The control plane is the state submitter. Consumers and workers may request a
change through a command, but PostgreSQL owns business state, approval,
budget, idempotency and audit facts.

## Event and command rules

The v1 event set includes project/version intake, gap identification, profile
confirmation, run start, task dispatch, evidence capture, finding submission,
evidence audit, approval, attention/failure, decision, dossier commit,
regression and completion. The command set is deliberately small and
product-agnostic: start a run, dispatch a task, request read-only evidence,
synthesize a decision, cancel a run and resolve an approval.

`SUBMISSION_UNKNOWN` is fail-closed: the event contract marks retry as blocked
and the API error code is non-retryable. T1 does not implement reconciliation or
any provider call.

## REST and SSE semantics

- Every REST write requires `Idempotency-Key` and `X-Correlation-Id` headers.
- A key is scoped to the tenant, operation and request hash. Replaying the same
  request returns the original accepted result; reusing it with a different
  request returns `IDEMPOTENCY_CONFLICT`.
- Responses echo `correlation_id` in the body where applicable and in the
  `X-Correlation-Id` response header.
- List endpoints use opaque `cursor`, bounded `limit`, `next_cursor` and
  `has_more`; clients must not decode or manufacture cursors.
- SSE frames use the durable event cursor as `id`. On reconnect the client
  sends `Last-Event-ID`; `cursor` is also accepted for clients that cannot set
  that header, and `Last-Event-ID` wins when both are present.
- The server replays events strictly after the cursor. With no cursor it sends
  a durable run snapshot followed by events from the current cursor. An expired
  cursor returns `CURSOR_INVALID`, after which the client refetches durable run
  state before opening a new stream.
- Error responses use one enum of error codes and always include a correlation
  id, retryability and structured details.

## UnifiedModel, Agent Identity and Skill

UnifiedModel is a semantic snapshot for handoff, external mapping and filtered
retrieval. Its `transaction_authority` explicitly names PostgreSQL. It does not
perform state transitions, budget deduction, approval, idempotency or audit
writes.

The embedded `AgentIdentity` contract freezes role, responsibilities, input and
output contracts, allowed Skill/Tool references, submit capabilities and
forbidden actions for the baseline 1+5 roles. An Agent can submit a structured
Finding, state-change request or MemoryCandidate; it cannot directly mutate
final state, long-term memory or a formal report.

The embedded `SkillContract` requires input/output schemas, preconditions,
permissions, failure classes, budget, evidence requirements, lifecycle and an
explicit `may_write_business_state: false`. `tier: P0` is schema-constrained to
the six baseline names:

1. `product-intake-normalizer`
2. `intake-gap-diagnosis`
3. `browser-product-audit`
4. `business-investment-assessment`
5. `evidence-grounding-audit`
6. `version-regression-verification`

The V2.0-only `user-validation-designer` is represented, when useful for
semantic comparison, as `REFERENCE`/`PROPOSED`; it is not an executable V0.1
P0 Skill. The reconciliation and promotion gate are in ADR 0002.

## Version and change policy

API, event, command, Skill, Prompt, Agent Identity, rule, UnifiedModel and Run
Manifest versions are independent. Major changes require a new major contract
file and an ADR. Minor changes may add optional fields or enum values only
when consumers remain tolerant. Patch changes correct descriptions or
non-semantic examples. Published schemas are immutable; use
Expand-Migrate-Contract for migrations. An event consumer must accept the
current and immediately previous released minor version. There is no earlier
published LaunchScope event artifact in this repository yet, so the T1
compatibility test guards the frozen envelope and additive-consumer rule; it is
not a claim of deployed backward compatibility.

## Local validation

From the repository root, the planned commands are:

```powershell
python -m pytest packages/contracts/tests/test_json_schema_contracts.py -q
python -m pytest packages/contracts/tests/test_event_compatibility.py -q
python -m jsonschema packages/contracts/events/envelope.schema.json packages/contracts/events/evaluation-events.v1.json
```

The test modules use `unittest.TestCase` and can also be executed without
pytest when the environment has the JSON Schema and YAML libraries:

```powershell
python -m unittest discover -s packages/contracts/tests -p "test_*.py" -q
```

These commands validate local files only. A green contract test is not a
browser, provider, paid, production or end-to-end acceptance result.
