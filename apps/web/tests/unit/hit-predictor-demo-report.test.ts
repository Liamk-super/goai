import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { demoSpecialists } from "../../src/lib/hit-predictor-demo-data.ts";

const report = readFileSync(new URL("../../src/components/reports/demo/HitPredictorDemoReport.tsx", import.meta.url), "utf8");
const agent = readFileSync(new URL("../../src/components/reports/demo/HitPredictorAgentDemo.tsx", import.meta.url), "utf8");
const data = readFileSync(new URL("../../src/lib/hit-predictor-demo-data.ts", import.meta.url), "utf8");
const landing = readFileSync(new URL("../../src/components/landing/PublicWheelLanding.tsx", import.meta.url), "utf8");

test("demo report keeps the v2.2 top-card order and probability disclaimer", () => {
  const index = report.indexOf("demoCopy.potentialIndex");
  const stage = report.indexOf("demoCopy.currentStage");
  const comparison = report.indexOf("demoCopy.comparedWithLast");
  const confidence = report.indexOf("demoCopy.conclusionConfidence");
  const recommendation = report.indexOf("demoCopy.currentRecommendation");
  assert.ok(index > 0 && index < stage && stage < comparison && comparison < confidence && confidence < recommendation);
  assert.match(data, /不是真实世界“成功概率”/);
  assert.match(data, /静态演示 · 不写入数据库/);
});

test("demo exposes four independent specialist reports with one summary/full source", () => {
  for (const code of ["user-validation", "product-technical", "business-investment", "evidence-calibration"]) {
    assert.match(data, new RegExp(`code: "${code}"`));
  }
  assert.match(report, /\/demo\/hit-predictor\/agents\/\$\{agent\.code\}/);
  assert.match(agent, /view === "summary" \? agent\.summarySections : agent\.fullSections/);
  assert.match(agent, /aria-current=\{view === "summary"/);
  assert.doesNotMatch(report + agent, /<iframe/i);
});

test("specialist reports preserve the complete source-report chapter depth", () => {
  const expectedDepth = {
    "user-validation": [4, 8],
    "product-technical": [6, 12],
    "business-investment": [7, 12],
    "evidence-calibration": [6, 7],
  } as const;

  for (const specialist of demoSpecialists) {
    const [summaryCount, fullCount] = expectedDepth[specialist.code];
    assert.equal(specialist.summarySections.length, summaryCount);
    assert.equal(specialist.fullSections.length, fullCount);
    assert.ok(specialist.fullSections.every(section => section.bullets.length >= 3));
  }
});

test("critical claims have inline citations and the source directory stays visible", () => {
  assert.match(report, /function CitationLinks/);
  assert.match(report, /id="evidence"/);
  assert.match(report, /sourceDirectory/);
  assert.match(data, /auditLabel: "需要补充"/);
});

test("landing includes a dedicated demo entry", () => {
  assert.match(landing, /href="\/demo\/hit-predictor"/);
  assert.match(data, /landingDemoZh: "查看演示报告"/);
});

test("demo attributes the reference to user-provided material without third-party branding", () => {
  assert.match(data, /来自用户提供的参考材料/);
  assert.doesNotMatch(data + report + agent, /千问|qwenwork/i);
});
