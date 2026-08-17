import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const runPage = readFileSync(
  new URL("../../src/app/(workspace)/runs/[runId]/page.tsx", import.meta.url),
  "utf8",
);
const fullPage = readFileSync(
  new URL("../../src/app/(workspace)/runs/[runId]/user-validation-report/page.tsx", import.meta.url),
  "utf8",
);
const apiClient = readFileSync(new URL("../../src/lib/api-client.ts", import.meta.url), "utf8");

test("summary and full reports render only in scriptless sandbox iframes", () => {
  for (const source of [runPage, fullPage]) {
    assert.match(source, /sandbox=""/u);
    assert.match(source, /srcDoc=/u);
    assert.doesNotMatch(source, /dangerouslySetInnerHTML/u);
  }
});

test("full report is an independent route with four authenticated downloads", () => {
  assert.doesNotMatch(fullPage, /<details/u);
  assert.match(runPage, /user-validation-report/u);
  assert.match(fullPage, /\["summary", "full"\]/u);
  assert.match(fullPage, /\["html", "markdown"\]/u);
  assert.match(apiClient, /user-validation-reports\/\$\{variant\}\?format=\$\{format\}/u);
});
