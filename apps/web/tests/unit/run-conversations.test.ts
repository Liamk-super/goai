import assert from "node:assert/strict";
import { test } from "node:test";

import { RUN_CONVERSATION_CHANNELS } from "../../src/lib/run-conversations.ts";

test("generation-v4 exposes the Supervisor and exactly three domain conversation channels", () => {
  assert.deepEqual(
    RUN_CONVERSATION_CHANNELS.map(item => item.channel),
    ["supervisor", "user-evidence", "product-engineering", "business-investment"],
  );
  assert.ok(!RUN_CONVERSATION_CHANNELS.some(item => item.channel === "geo-policy-trend" as never));
  assert.ok(!RUN_CONVERSATION_CHANNELS.some(item => item.channel === "evidence-auditor" as never));
});
