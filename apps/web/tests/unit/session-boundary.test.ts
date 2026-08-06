import assert from "node:assert/strict";
import test from "node:test";

import { sessionFromDocument } from "../../src/lib/api-client.ts";
import { DEMO_SESSION_KEY, loadDemoSession, parseDemoSession } from "../../src/lib/demo-session.ts";

test("workspace API fails closed when no local Demo session exists", () => {
  const original = globalThis.window;
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { localStorage: { getItem: () => null, removeItem: () => undefined } },
  });
  try {
    assert.throws(() => sessionFromDocument(), /No local Demo workspace session/);
  } finally {
    Object.defineProperty(globalThis, "window", { configurable: true, value: original });
  }
});

test("damaged or old Demo sessions are rejected and removed", () => {
  assert.equal(parseDemoSession("not-json"), null);
  const removed: string[] = [];
  const storage = { getItem: () => JSON.stringify({ schemaVersion: "v0" }), removeItem: (key: string) => removed.push(key), setItem: () => undefined };
  assert.equal(loadDemoSession(storage), null);
  assert.deepEqual(removed, [DEMO_SESSION_KEY]);
});
