import assert from "node:assert/strict";
import { test } from "node:test";
import { buildAgentTabs, SPECIALIST_AGENTS } from "../../src/lib/agent-tabs.ts";
import type { Clarification } from "../../src/lib/api-client.ts";

const question = (agent: string, requestId: string): Clarification => ({
  request_id: requestId,
  task_id: `task-${requestId}`,
  agent_code: agent,
  field: "payer",
  question: "Who signs the contract?",
  why_blocking: "Cannot grade willingness to pay.",
  impact_dimension: "BUSINESS_INVESTMENT",
});

const task = (ref: string, status: string, evidence = 0) => ({
  id: `id-${ref}-${status}`,
  stage_code: "DOMAIN_REVIEW",
  agent_identity_ref: ref,
  status,
  tool_allowlist: [] as string[],
  evidence_count: evidence,
});

test("only the four domain specialists are surfaced as user-facing tabs", () => {
  const tabs = buildAgentTabs({ tasks: [] }, []);

  assert.equal(tabs.length, 4);
  assert.deepEqual(
    tabs.map(tab => tab.code),
    SPECIALIST_AGENTS.map(agent => agent.code),
  );
  // The supervisor and the calibration Agent never ask the user for product facts.
  assert.ok(!tabs.some(tab => tab.code === "evaluation-manager"));
  assert.ok(!tabs.some(tab => tab.code === "evidence-auditor"));
  assert.deepEqual(tabs.slice(0, 3).map(tab => tab.name), ["产品经理", "用户", "投资人"]);
});

test("an open question marks exactly its own Agent as blocked", () => {
  const tabs = buildAgentTabs(
    { tasks: [task("business-investment@2.0", "NEEDS_INPUT"), task("user-evidence@2.0", "SUCCEEDED")] },
    [question("business-investment", "r1")],
  );

  const blocked = tabs.find(tab => tab.code === "business-investment");
  const other = tabs.find(tab => tab.code === "user-evidence");

  assert.equal(blocked?.pending.length, 1);
  assert.equal(blocked?.status, "NEEDS_INPUT");
  assert.equal(other?.pending.length, 0);
  assert.equal(other?.status, "SUCCEEDED");
  assert.equal(tabs.filter(tab => tab.pending.length > 0).length, 1);
});

test("the badge counts every open question for one Agent", () => {
  const tabs = buildAgentTabs(
    { tasks: [task("geo-policy-trend@2.0", "NEEDS_INPUT")] },
    [question("geo-policy-trend", "r1"), question("geo-policy-trend", "r2")],
  );

  assert.equal(tabs.find(tab => tab.code === "geo-policy-trend")?.pending.length, 2);
});

test("evidence counts aggregate across an Agent's tasks", () => {
  const tabs = buildAgentTabs(
    { tasks: [task("product-engineering@2.0", "SUCCEEDED", 3), task("product-engineering@2.0", "SUCCEEDED", 2)] },
    [],
  );

  assert.equal(tabs.find(tab => tab.code === "product-engineering")?.evidence, 5);
});

test("a resumed Agent no longer shows a quest badge", () => {
  const answered = buildAgentTabs({ tasks: [task("business-investment@2.0", "READY")] }, []);

  const tab = answered.find(item => item.code === "business-investment");
  assert.equal(tab?.pending.length, 0);
  assert.notEqual(tab?.status, "NEEDS_INPUT");
});
