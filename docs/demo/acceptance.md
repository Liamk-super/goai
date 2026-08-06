# LaunchScope v0.2 acceptance boundary

Local automated tests prove contracts, RLS persistence, idempotency, DAG gates,
rule ownership, budget failure behavior, Web rendering and script syntax. They
do not prove a provider, browser target, search account or AgentTeams deployment.

Live acceptance is complete only when one sanitized Run-linked bundle proves:

- official AgentTeams v1.2.0 Controller/Manager, Human, Team and six online
  Workers;
- published RocketMQ event plus Inbox and Matrix event receipts;
- at least three distinct specialist handoffs, targeting all four dimensions;
- a real allowlisted browser capture and search result with private object hash,
  source, region, fetch time and validity;
- an append-only Auditor downgrade/rejection without Finding rewrite;
- rule-generated four-dimension report, trend, contradictions and at most three
  Evidence-linked actions, plus same-standard V1/V2 change;
- reconciled model/search usage at or below USD 20, with no unexplained
  PENDING/CLAIMED or unknown billing state.

Without an authorized URL and model/search credentials, the correct result is
`BLOCKED_NO_AUTHORIZED_CASE`. Export with `scripts/export-v01-acceptance.py`;
the generated `snapshot-metadata.json` labels it “Recorded acceptance snapshot”
and computes `external_e2e_claim` only from live execution mode, bindings,
Matrix receipts and fully published Outbox facts.
