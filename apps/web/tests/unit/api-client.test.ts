import assert from "node:assert/strict";
import test from "node:test";

import { LaunchScopeApi, boundedIdempotencyKey, stableAsciiKey } from "../../src/lib/api-client.ts";

test("non-ASCII material names map to stable header-safe idempotency keys", () => {
  const first = stableAsciiKey("20260329 大学生创业训练、创业实践项目申报书(2).pdf");
  const replay = stableAsciiKey("20260329 大学生创业训练、创业实践项目申报书(2).pdf");
  assert.equal(first, replay);
  assert.match(first, /^[0-9a-f]{8}$/);
  assert.notEqual(first, stableAsciiKey("20260329 公司化运作情况报告(2).pdf"));
});

test("material selection idempotency keys stay bounded as the selection grows", () => {
  const payload = JSON.stringify(Array.from({ length: 24 }, (_, index) => ({
    material_id: crypto.randomUUID(),
    analysis_id: crypto.randomUUID(),
    decision: index % 2 ? "INCLUDE" : "INCLUDE_PARTIAL",
  })));
  const first = boundedIdempotencyKey("material-selection", crypto.randomUUID(), payload);
  const replay = boundedIdempotencyKey("material-selection", first.split(":")[1], payload);

  assert.equal(first, replay);
  assert.ok(first.length <= 200);
  assert.match(first, /^material-selection:[0-9a-f-]{36}:[0-9a-f]{8}$/);
});

test("completed Run report lookup prefers immutable v3, then v2, and falls back only on 404", async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  globalThis.fetch = (async (input: string | URL | Request) => {
    const url = String(input);
    calls.push(url);
    if (url.endsWith("/experience/v3/runs/run-1/report") || url.endsWith("/experience/v2/runs/run-1/report")) {
      return new Response(JSON.stringify({ error_code: "NOT_FOUND" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ report_id: "legacy-report" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  try {
    const report = await new LaunchScopeApi({ tenantId: "tenant", actorId: "actor" }).getReportForRunDisplay("run-1");
    assert.equal(report.report_id, "legacy-report");
    assert.deepEqual(calls.map(url => new URL(url, "http://local").pathname), [
      "/api/v1/experience/v3/runs/run-1/report",
      "/api/v1/experience/v2/runs/run-1/report",
      "/api/v1/experience/runs/run-1/report",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
