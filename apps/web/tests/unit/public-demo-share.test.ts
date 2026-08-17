import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const reportPage = readFileSync(new URL("../../src/app/(public)/shared/demo/[token]/reports/[reportId]/page.tsx", import.meta.url), "utf8");
const runPage = readFileSync(new URL("../../src/app/(public)/shared/demo/[token]/runs/[runId]/page.tsx", import.meta.url), "utf8");
const agentPage = readFileSync(new URL("../../src/app/(public)/shared/demo/[token]/runs/[runId]/agent-reports/[agentCode]/page.tsx", import.meta.url), "utf8");
const evidencePage = readFileSync(new URL("../../src/app/(public)/shared/demo/[token]/runs/[runId]/evidence/[evidenceId]/page.tsx", import.meta.url), "utf8");
const publicLayout = readFileSync(new URL("../../src/app/(public)/layout.tsx", import.meta.url), "utf8");

test("public Demo share pages never read or create a local Demo session", () => {
  for (const source of [reportPage, runPage, agentPage, evidencePage]) {
    assert.doesNotMatch(source, /sessionFromDocument|browserApi|DemoSessionGuard|demo-login/);
    assert.match(source, /\/api\/v1\/public\/demo\//);
  }
});

test("public Demo report is explicitly read-only", () => {
  assert.match(reportPage, /readOnly/);
  assert.doesNotMatch(reportPage, /new-evaluation/);
});

test("public evidence keeps integrity metadata out of the audience projection", () => {
  assert.doesNotMatch(evidencePage, /evidence\.sha256|t\("Integrity"\)/);
});

test("public routes opt out of search indexing without claiming confidentiality", () => {
  assert.match(publicLayout, /robots: \{ index: false, follow: false \}/);
  assert.doesNotMatch(publicLayout, /confidential|private/);
});
