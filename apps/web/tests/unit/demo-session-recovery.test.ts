import assert from "node:assert/strict";
import { test } from "node:test";

import { restoreDemoSession } from "../../src/lib/demo-session-recovery.ts";
import { DEMO_SESSION_KEY, DEMO_SESSION_SCHEMA, type DemoSession } from "../../src/lib/demo-session.ts";

const fixedSession: DemoSession = {
  schemaVersion: DEMO_SESSION_SCHEMA,
  tenantId: "tenant-fixed",
  workspaceId: "workspace-fixed",
  actorId: "local-demo:fixed",
  displayName: "Fixed Demo",
  createdAt: "2026-08-12T00:00:00Z",
};

function storage(initial?: string) {
  const values = new Map<string, string>();
  if (initial) values.set(DEMO_SESSION_KEY, initial);
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
}

test("missing browser session restores and caches the fixed Demo workspace", async t => {
  const local = storage();
  const calls: string[] = [];
  t.mock.method(globalThis, "fetch", async input => {
    calls.push(String(input));
    return new Response(JSON.stringify(fixedSession), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });

  const restored = await restoreDemoSession(local as Storage);

  assert.deepEqual(restored, fixedSession);
  assert.equal(calls.length, 1);
  assert.ok(calls[0].endsWith("/api/v1/demo/default-session"));
  assert.equal(local.getItem(DEMO_SESSION_KEY), JSON.stringify(fixedSession));
});

test("invalid cached membership is replaced by the fixed binding, never by session creation", async t => {
  const local = storage(JSON.stringify({ ...fixedSession, actorId: "local-demo:stale" }));
  const calls: string[] = [];
  t.mock.method(globalThis, "fetch", async input => {
    calls.push(String(input));
    if (calls.length === 1) return new Response("{}", { status: 404 });
    return new Response(JSON.stringify(fixedSession), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });

  await restoreDemoSession(local as Storage);

  assert.equal(calls.length, 2);
  assert.ok(calls[0].endsWith("/api/v1/demo/session"));
  assert.ok(calls[1].endsWith("/api/v1/demo/default-session"));
  assert.ok(calls.every(path => !path.endsWith("/api/v1/demo/sessions")));
});
