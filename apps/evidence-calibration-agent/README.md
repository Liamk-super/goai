# Evidence Calibration Agent

Thin Agent layer for `evidence-grounding-audit`. It owns identity, task wrapper, context assembly, Skill invocation, terminal status, and SupervisorHandoff delivery. The reusable audit logic remains in `packages/evidence-grounding-audit`.

Run with `node apps/evidence-calibration-agent/runner/cli.mjs < packages/evidence-grounding-audit/examples/demo.input.json`.
