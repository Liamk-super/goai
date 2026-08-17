# LaunchScope generation-aware Demo runbook

## Select the admission generation

Keep secrets only in the untracked `.env.demo.local`. For a new supervisor 1+4 acceptance Run, set:

```dotenv
LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED=true
```

`true` selects `launchscope-team-v4.yaml`, `generated/packages-v4`, five v4 Workers, and the v4 Team/Human. `false`
or an unset value preserves the legacy `launchscope-team.yaml`, six Workers, and historical admission behavior.
A Run remains pinned to its frozen Manifest and is never converted in place.

## Preflight and start

```powershell
pwsh -File scripts/demo-bootstrap.ps1 -InstallAgentTeams
pwsh -File scripts/demo-preflight.ps1 -RequireExternalCase
pwsh -File scripts/demo-start.ps1
```

The preflight imports `.env.demo.local`, validates the selected package and resource, expects exactly five v4 or
six legacy Workers, discovers one usage-counter endpoint per selected Worker, checks the cost mode, token/call
limits, live credentials, browser/search authorization, ports, Chromium, and migration head. `EXACT` additionally
requires model prices for the USD 20 gate; `TOKEN_ONLY` does not. Do not start a live Run unless every required
check passes.

The start path disables every LaunchScope heartbeat, disables CoPaw/SDK model retries, sets one in-flight model call
per Worker delivery, and verifies that every selected Worker points only to `launchscope-model-egress`. When no open
legacy Run remains, new Runs freeze `model_accounting.mode=GATEWAY_DELIVERY`; each delivery receives an opaque
credential and the generic credential refresher stays off. If an open legacy Run still depends on
`COPAW_TASK_DELTA`, startup retains the gated generic credential path for that generation and does not switch its
accounting in place.

Use `demo-start.ps1 -RecordedOnly` only for the labelled read-only fallback. It does not start the Outbox publisher
or AgentTeams bridges and cannot satisfy live acceptance. `-MaterialOnly` is a real private-material path but does
not prove public browser/search Evidence.

Before restarting an existing local stack, inspect `.demo/run/*.pid.json`, process command lines, ports, Docker
containers, migrations, AgentTeams resources, and unsafe Run/Outbox state. Stop only processes whose PID, start
time, executable, and repository marker match the Demo records:

```powershell
pwsh -File scripts/demo-stop.ps1 -KeepInfrastructure
pwsh -File scripts/demo-start.ps1
```

## Generation-v4 runtime truth path

1. Intake persists RequirementBriefV1; critical ambiguity stays in the single supervisor conversation.
2. The supervisor proposes ManagerPlanV1. The control plane validates it and creates the user, product, and
   investment Tasks; first-round domain Workers cannot mention or dispatch peers.
3. The publisher claims only committed Outbox rows. Broker ACK is required for `PUBLISHED`; ambiguous ACK freezes
   the Run as `SUBMISSION_UNKNOWN` with no retry.
4. Inbox dedupe and an event-ID-derived Matrix transaction deliver each assignment. Sender MXID, tenant, Run,
   Task, Agent role, and Agent Identity must validate before durable receipt/handoff writes.
5. Legal domain terminal states unlock the Auditor serially. At most one targeted remediation and one re-audit may
   occur before deterministic scoring and supervisor synthesis.
6. Reference validation and backend rendering atomically commit Decision, Report, and Project Dossier before
   `COMPLETED`. Provider submission and call/token usage must be known. Provider cost is required only in `EXACT`;
   `TOKEN_ONLY` persists `model_cost_unavailable` without blocking completion.
7. The gateway records every model invocation, while the Task handoff/timeout reconciler performs one financial
   posting per delivery. CoPaw cumulative counters are advisory reconciliation evidence and never create a second
   usage or budget debit.

## Evidence export and restart check

```powershell
.venv\Scripts\python.exe scripts/export-v01-acceptance.py `
  --tenant-id <tenant-id> --project-id <project-id> `
  --run-id <golden-run-id> --run-id <clarification-run-id> `
  --output deliverables/m7-b/<acceptance-directory>
pwsh -File scripts/demo-stop.ps1 -KeepInfrastructure
pwsh -File scripts/demo-start.ps1
```

After restart, reopen the project, both Runs, and the golden report in real Chromium. Confirm object SHA readback,
database terminal state, usage totals, the selected cost-mode record, and absence of duplicate Task, tool, delivery,
or usage rows. Require billing-row reconciliation only for `EXACT`.

## Feature-flag rollback

Set `LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED=false`, restart the verified local application path, and confirm that new v4
admission is refused while completed v4 Runs and historical 1+5 Runs remain readable. Do not delete v4 Workers or
data until incompatible in-flight deliveries have been proven drained.

## Failure handling and stop

- Never retry, fail over, change model/provider, resubmit, or manually settle when submission, required token usage,
  exact-mode billing, or paid timeout state is unknown. Declared `TOKEN_ONLY` cost unavailability is not an unknown
  submission and does not block downstream work.
- Never edit Run, Task, Outbox, usage, budget, or approval rows with raw SQL to recover acceptance.
- Do not reset while a Run is `RUNNING`/`NEEDS_ATTENTION`, an Outbox row is `CLAIMED`, or any
  `SUBMISSION_UNKNOWN` fact exists.
- Keep tokens, keys, private bodies, prompts, and model reasoning out of logs, resources, browser storage, and
  evidence bundles.

```powershell
pwsh -File scripts/demo-stop.ps1
```

`demo-stop.ps1` preserves volumes. `demo-reset.ps1 -Force` is restricted to `local-demo` and is not part of normal
acceptance recovery.
## v2.2 report export operations

Set `LAUNCHSCOPE_REPORT_RENDER_WEB_URL` to the reachable Web origin used by the API-side Chromium renderer. The local startup default is `http://127.0.0.1:3000`.

Report export POSTs require `Idempotency-Key` and `X-Correlation-Id`. A completed export is cached by canonical report SHA, renderer version, locale, view, and Evidence-inclusion choice. A renderer failure updates only `report_export_artifact`; it never reruns an Agent, model, search, or report synthesis. Retry the failed request with the same idempotency key after correcting Web/Chromium availability.

The complete package contains five PDFs, `来源目录.html`, `来源目录.json`, and `manifest.json`. When Evidence originals are requested, only hash-verified objects are included. Missing or mismatched objects remain explicit entries in `evidence-index.json`; never replace them with empty files.
