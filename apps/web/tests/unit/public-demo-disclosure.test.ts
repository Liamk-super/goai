import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const component = readFileSync(
  new URL("../../src/components/forms/PublicDemoDisclosure.tsx", import.meta.url),
  "utf8",
);
const intakePage = readFileSync(
  new URL("../../src/app/(workspace)/projects/[projectId]/new-evaluation/page.tsx", import.meta.url),
  "utf8",
);
const translations = readFileSync(new URL("../../src/lib/i18n.ts", import.meta.url), "utf8");

test("disclosure uses the approved one-button copy without a checkbox", () => {
  assert.match(translations, /公开 Demo：上传材料可能在报告证据链中公开展示。/);
  assert.match(translations, /我已了解，继续上传/);
  assert.doesNotMatch(component, /type="checkbox"/);
});

test("material upload waits for durable disclosure acceptance", () => {
  assert.match(intakePage, /await ensurePublicDemoDisclosure\(activeVersion\)/);
  assert.match(intakePage, /acceptPublicDemoDisclosure/);
  assert.match(intakePage, /publicDemoDisclosureAccepted/);
});

test("first evaluation intake hides version jargon and security ceremony", () => {
  assert.match(intakePage, /versionLabel !== "V1" &&/);
  assert.doesNotMatch(intakePage, /t\("Original file and SHA-256"\)/);
  assert.match(intakePage, /t\("Original file stored privately"\)/);
});

test("quick visual preview never reports more completed pages than its two-page cap", () => {
  assert.match(intakePage, /Math\.min\(visualUnderstood, 2\)/);
  assert.match(intakePage, /Math\.min\(visualCandidates, 2\)/);
});

test("persisted material analyses remain reviewable after a browser reload", () => {
  assert.match(intakePage, /setServerAnalyses\(items\)/);
  assert.match(intakePage, /listMaterialAnalyses\(versionId\)/);
  assert.match(intakePage, /persistedAnalyses\.map/);
  assert.match(intakePage, /materialDecisions\[item\.analysis_id\] === "INCLUDE_PARTIAL"/);
  assert.doesNotMatch(intakePage, /String\(locator\.reason \?\? t\("Not covered"\)\)/);
});
