import assert from "node:assert/strict";
import test from "node:test";
import { runAgent } from "../src/index.mjs";

test("Agent invokes the Skill and returns SupervisorHandoff", () => {
  const output = runAgent({ task_id: "agent-test", project_id: "p", product_version: "v1", generated_at: "2026-08-11T00:00:00Z", agent_results: [{ source_agent: "product", status: "BLOCKED", payload: {} }] });
  assert.equal(output.status, "SUCCEEDED");
  assert.equal(output.skill_invocation.completed, true);
  assert.equal(output.supervisor_handoff.audit_coverage.partial_audit, true);
});
