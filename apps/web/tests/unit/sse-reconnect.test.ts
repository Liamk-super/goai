import assert from "node:assert/strict";
import test from "node:test";

import { DurableRunStream } from "../../src/lib/sse-client.ts";

const encoder = new TextEncoder();
const sse = (body: string) => new Response(new ReadableStream({ start(controller) { controller.enqueue(encoder.encode(body)); controller.close(); } }));

test("reconnect uses the last durable cursor and does not re-emit an event", async () => {
  const requests: RequestInit[] = []; const events: string[] = [];
  const fetcher = async (_input: RequestInfo | URL, init?: RequestInit) => {
    requests.push(init ?? {});
    return sse(requests.length === 1 ? "event: run.snapshot\nid: event.a\ndata: {\"status\":\"PLANNED\"}\n\nevent: run.status_changed\nid: event.b\ndata: {\"status\":\"RUNNING\"}\n\n" : "event: run.status_changed\nid: event.c\ndata: {\"status\":\"COMPLETED\"}\n\n");
  };
  const stream = new DurableRunStream("/runs/r/events", {}, { onSnapshot() {}, onEvent: event => events.push(event.id), onError: error => { throw error; } }, fetcher);
  await stream.connect(); await stream.connect();
  assert.equal(new Headers(requests[1]?.headers).get("Last-Event-ID"), "event.b");
  assert.deepEqual(events, ["event.b", "event.c"]);
});

test("invalid cursor refetches the durable snapshot before reopening the stream", async () => {
  const snapshots: Record<string, unknown>[] = [];
  let calls = 0;
  const stream = new DurableRunStream("/runs/r/events", {}, { onSnapshot: snapshot => snapshots.push(snapshot), onEvent() {}, onError: error => { throw error; } }, async () => {
    calls += 1;
    return calls === 1 ? new Response("", { status: 409 }) : sse(`event: run.snapshot
id: event.fresh
data: {"status":"PLANNED"}

`);
  }, async () => ({ current_cursor: "event.fresh", status: "PLANNED" }));
  await stream.connect();
  assert.deepEqual(snapshots, [{ current_cursor: "event.fresh", status: "PLANNED" }, { status: "PLANNED" }]);
});

test("a normal finite stream closure activates the durable snapshot fallback", async () => {
  let closed = 0;
  const stream = new DurableRunStream("/runs/r/events", {}, {
    onSnapshot() {},
    onEvent() {},
    onError: error => { throw error; },
    onClosed: () => { closed += 1; },
  }, async () => sse(`event: run.snapshot
id: event.initial
data: {"status":"RUNNING"}

`));

  await stream.connect();

  assert.equal(closed, 1);
});
