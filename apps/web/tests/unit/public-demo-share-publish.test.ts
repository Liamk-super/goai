import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const action = readFileSync(new URL("../../src/components/reports/v2/PublicDemoShareAction.tsx", import.meta.url), "utf8");
const supervisor = readFileSync(new URL("../../src/components/reports/v2/SupervisorReportV2.tsx", import.meta.url), "utf8");
const client = readFileSync(new URL("../../src/lib/api-client.ts", import.meta.url), "utf8");

test("private supervisor report publishes one full read-only public link", () => {
  assert.match(supervisor, /!readOnly && <PublicDemoShareAction/);
  assert.match(action, /createPublicDemoShare/);
  assert.match(action, /shared\/demo/);
  assert.match(action, /Open public report/);
  assert.doesNotMatch(action, /include_agent_reports|include_evidence/);
});

test("public share publication uses the additive versioned write endpoint", () => {
  assert.match(client, /experience\/v2\/reports\/\$\{reportId\}\/public-demo-share/);
  assert.match(client, /public-demo-share-v1/);
});
