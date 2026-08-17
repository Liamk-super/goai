# ADR 0011: Token-only provider cost accounting

- Status: Accepted
- Date: 2026-08-12
- Decision owners: LaunchScope product owner and evaluation control plane
- Clarifies: ADR 0010 failure policy

## Context

Some authorized model gateways expose stable submission results, model identity, call counts, and token counters but
do not expose a provider invoice or independently reconcilable charged amount. Generation-v4 previously treated a
missing USD conversion as `SUBMISSION_UNKNOWN`, even when the provider result and token interval were known. That
classification blocked downstream work without reducing duplicate-submission risk.

LaunchScope still needs bounded execution and must never retry a request whose submission state is uncertain. It
does not, however, need an exact provider invoice to determine whether a known model result may advance through the
control plane.

## Decision

`LAUNCHSCOPE_PROVIDER_COST_MODE` supports `TOKEN_ONLY` and `EXACT` and defaults to `TOKEN_ONLY`.

In `TOKEN_ONLY` mode:

- provider submission identity/result and task-attributable call/token counters remain required;
- missing provider cost does not transition a Task or Run to `NEEDS_ATTENTION`;
- the control plane persists model tokens, model calls, and an explicit `model_cost_unavailable` usage record;
- no USD amount is invented and the USD reservation is not consumed when cost is unavailable;
- hard model-call, input-token, output-token, search, browser, timeout, and remediation limits remain enforced;
- functional external E2E may complete, while billing reconciliation is reported separately as unavailable.

In `EXACT` mode, both configured prices or a complete provider cost receipt remain required and the existing USD
budget gate remains active.

`SUBMISSION_UNKNOWN` remains fail-closed in every mode. Missing or invalid submission identity, unknown result,
missing required token counters, counter rollback, receipt reuse, paid timeout with uncertain provider state, and
uncertain external side effects still prohibit automatic retry, failover, replacement submission, or raw-SQL
settlement. An Intake adapter that cannot distinguish response validation from an unknown provider submission is
not exempted by this decision.

Run Manifest v4 is not edited. Its root permits additive configuration, so `model_pricing.cost_mode` and the Task
usage policy freeze the selected mode without changing released failure-policy constants. `BILLING_UNKNOWN` retains
its existing meaning for a Run that selected `EXACT`; cost absence in `TOKEN_ONLY` is an expected declared condition,
not an unknown billing failure.

## Consequences

### Positive

- Providers without invoice APIs no longer block otherwise verifiable AgentTeams execution.
- Submission safety and token/call quotas remain fail-closed.
- Reports do not misrepresent an unavailable charge as zero or as reconciled billing.

### Negative

- The USD 20 reservation cannot be the effective runtime guard for token-only calls without a cost receipt.
- Operations and acceptance exports must distinguish functional proof from billing proof.

### Neutral

- Environments that can supply exact prices may opt into `EXACT` without changing workflow topology.
- Historical Runs retain their frozen configuration and terminal state.

## Alternatives considered

### Disable provider usage accounting entirely

Rejected because it would discard the available token and call evidence and weaken quota enforcement.

### Persist unavailable cost as a reconciled zero-dollar charge

Rejected because zero would be a false billing claim. A separate durable status record is required.

### Continue requiring exact provider billing for every functional acceptance

Rejected because the provider cannot supply it and the amount is not needed to validate a known model result.

## References

- `docs/adr/0010-supervisor-agent-one-plus-four-generation.md`
- `apps/api/src/launchscope_api/modules/evaluation/agentteams_usage.py`
- `apps/api/src/launchscope_api/modules/evaluation/handoff_application.py`
- `scripts/demo-preflight.ps1`
