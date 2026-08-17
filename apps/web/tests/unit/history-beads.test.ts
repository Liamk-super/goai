import assert from "node:assert/strict";
import { test } from "node:test";
import { beadSignal, beadTone, buildHistoryBeads } from "../../src/lib/history-beads.ts";
import type { Project, Run } from "../../src/lib/api-client.ts";

const project = (id: string, name: string, status = "ACTIVE"): Project => ({
  project_id: id,
  workspace_id: "ws-1",
  name,
  status,
});

const run = (id: string, status: string, projectId = "p-1", updatedAt = "2026-08-12T00:00:00Z"): Run => ({
  run_id: id,
  project_id: projectId,
  product_version_id: "v-1",
  status,
  standard_version: "1.0",
  current_cursor: "event.initial",
  correlation_id: "c-1",
  updated_at: updatedAt,
});

test("beads are a projection of individual Runs, capped at the limit", () => {
  const projects = Array.from({ length: 8 }, (_, i) => project(`p-${i}`, `项目 ${i}`));
  const runs = Object.fromEntries(projects.map((item, index) => [
    item.project_id,
    [run(`r-${index}`, "RUNNING", item.project_id, `2026-08-${String(index + 1).padStart(2, "0")}T00:00:00Z`)],
  ]));
  const beads = buildHistoryBeads(projects, runs, 6);
  assert.equal(beads.length, 6);
  assert.equal(beads[0].runId, "r-7");
});

test("version and navigation target come from each durable Run", () => {
  const beads = buildHistoryBeads(
    [project("p-1", "评审对象")],
    { "p-1": [{ ...run("r-2", "COMPLETED"), product_version_label: "V2" }, run("r-1", "COMPLETED", "p-1", "2026-08-11T00:00:00Z")] },
  );
  assert.equal(beads[0].version, "V2");
  assert.equal(beads[0].runId, "r-2");
  assert.equal(beads[0].status, "COMPLETED");
  assert.equal(beads[0].signal, "已完成");
});

test("a project without runs does not fabricate a history bead", () => {
  const beads = buildHistoryBeads([project("p-1", "新项目", "DRAFT")], {});
  assert.equal(beads.length, 0);
});

test("bead signals map durable statuses without fabricating progress", () => {
  assert.equal(beadSignal("RUNNING"), "预测中");
  assert.equal(beadSignal("WAITING_FOR_USER"), "等待回答");
  assert.equal(beadSignal("PLANNED"), "已规划");
  assert.equal(beadSignal("SOMETHING_ELSE"), "档案");
});

test("bead tone highlights completion and attention only", () => {
  assert.equal(beadTone("COMPLETED"), "completed");
  assert.equal(beadTone("WAITING_FOR_USER"), "attention");
  assert.equal(beadTone("RUNNING"), "idle");
});
