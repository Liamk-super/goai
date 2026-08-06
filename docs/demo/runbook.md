# LaunchScope v0.2 Demo runbook

## Commands

```powershell
pwsh -File scripts/demo-bootstrap.ps1 -InstallAgentTeams
pwsh -File scripts/demo-preflight.ps1 -RequireExternalCase
pwsh -File scripts/demo-start.ps1
pwsh -File scripts/demo-stop.ps1
```

Use `demo-start.ps1 -RecordedOnly` only for the labelled read-only fallback.
It does not start the Outbox publisher or AgentTeams bridges. `demo-stop.ps1`
matches PID, process start time and executable before stopping a process and
preserves all volumes. `demo-reset.ps1 -Force` is restricted to local-demo,
checks unsafe Run/Outbox states, deletes only the named Compose volumes and
frozen 1+5 resources, and does not uninstall AgentTeams.

## Runtime truth path

1. Dispatch transaction freezes Manifest, prices/limits, USD 20 reservation,
   4 stages, 7 Tasks, AgentTeams binding and Outbox event.
2. The `launchscope_publisher` role claims only committed Outbox rows. Broker
   ACK is required for `PUBLISHED`; an ambiguous ACK freezes Outbox and Run as
   `SUBMISSION_UNKNOWN`, with no retry.
3. The RocketMQ bridge uses Inbox dedupe and an event-ID-derived Matrix
   transaction ID. AgentTeams Manager routes the Human assignment to the fixed
   Team leader.
4. The Matrix listener never advances its sync cursor until the authenticated
   ingress accepts the event. Sender MXID, tenant, Run, Task and Agent role are
   validated before the append-only receipt/handoff is written.
5. Four successful domain Tasks unlock Auditor; Auditor appends decisions;
   Auditor completion unlocks synthesis. Provider usage must be known. The
   control-plane rule layer, not a model, writes Decision and Report.

## Failure handling

- Never retry, fail over, change model/runtime/provider, resubmit, or manually
  settle when submission/usage/billing is unknown.
- Never edit Run, Task, Outbox, usage or budget rows with raw SQL to recover a
  Demo. Preserve the facts and investigate through hashes and receipts.
- Do not reset while a Run is RUNNING/NEEDS_ATTENTION, an Outbox row is CLAIMED,
  or any `SUBMISSION_UNKNOWN` fact exists.
- Secrets stay only in `.env.demo.local` or AgentTeams Secret storage; logs,
  CRs, browser storage and acceptance bundles must remain body/token free.
