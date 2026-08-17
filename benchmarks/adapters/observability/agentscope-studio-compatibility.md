# AgentScope Studio compatibility check

- Checked: 2026-08-09
- Candidate: AgentScope Studio v1.0.x
- Result: BLOCKED_NO_DOCUMENTED_GENERIC_OTLP_INGEST
- Live UI evidence: not produced

Official AgentScope documentation says tracing is OpenTelemetry-based, but it distinguishes two integration modes:
Studio is connected through `agentscope.init(studio_url=...)`, while generic OTLP-compatible third-party backends are
connected through `tracing_url`. The Studio quick start likewise requires an AgentScope application and `studio_url`.

LaunchScope executes through AgentTeams and currently emits to the repository OpenTelemetry Collector. No official
document found in this check defines a generic Studio OTLP receiver that the existing Collector can target. Therefore
the existing `infra/observability/agentscope-studio.yaml` descriptor is not sufficient evidence of ingestion or UI
display, and installing Studio alone would not prove the requested path.

Benchmark V1 keeps:

- the existing OTel allowlist/body guard as the privacy boundary;
- a default-off Studio descriptor;
- explicit acceptance fields for pinned version, ingestion proof and visible trace hierarchy.

It does not add AgentScope as a second execution runtime or claim a trace screenshot. A future approved compatibility
task must either prove a documented Studio bridge or add a separately governed adapter, then capture a sanitized trace
ID and UI evidence.

Official sources:

- https://doc.agentscope.io/tutorial/task_tracing.html
- https://doc.agentscope.io/tutorial/task_studio.html
- https://github.com/agentscope-ai/agentscope-studio

