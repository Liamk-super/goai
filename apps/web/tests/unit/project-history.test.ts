import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type { Project, Run } from "../../src/lib/api-client.ts";
import { filterProjectRuns, filterProjects, runVersionLabel } from "../../src/lib/project-history.ts";

const projects: Project[] = [
  { project_id: "project-1", workspace_id: "workspace", name: "AIGC 营销平台", status: "ACTIVE" },
  { project_id: "project-2", workspace_id: "workspace", name: "CreaTrades", status: "ACTIVE" },
];

const runs: Run[] = [
  { run_id: "run-3", project_id: "project-1", product_version_id: "version-3", status: "COMPLETED", standard_version: "1", current_cursor: "3", correlation_id: "c3", product_version_label: "V3", product_version_number: 3 },
  { run_id: "run-2", project_id: "project-1", product_version_id: "version-2", status: "RUNNING", standard_version: "1", current_cursor: "2", correlation_id: "c2", product_version_label: null, product_version_number: 2 },
  { run_id: "run-1", project_id: "project-1", product_version_id: "version-1", status: "PLANNED", standard_version: "1", current_cursor: "1", correlation_id: "c1", product_version_label: null, product_version_number: null },
];

test("project search is case-insensitive and preserves project-level results", () => {
  assert.deepEqual(filterProjects(projects, "crea"), [projects[1]]);
  assert.deepEqual(filterProjects(projects, "营销"), [projects[0]]);
  assert.equal(filterProjects(projects, "").length, 2);
});

test("version search matches durable labels, numbers, and statuses within one project", () => {
  assert.deepEqual(filterProjectRuns(runs, "V2").map(run => run.run_id), ["run-2"]);
  assert.deepEqual(filterProjectRuns(runs, "completed").map(run => run.run_id), ["run-3"]);
  assert.equal(runVersionLabel(runs[2], 1), "V1");
});

test("project archive stays within the evaluation history API page limit", () => {
  const source = readFileSync("apps/web/src/app/(workspace)/projects/page.tsx", "utf8");
  assert.match(source, /listEvaluationHistory\(\{ limit: 50 \}\)/);
  assert.doesNotMatch(source, /listEvaluationHistory\(\{ limit: 100 \}\)/);
});
