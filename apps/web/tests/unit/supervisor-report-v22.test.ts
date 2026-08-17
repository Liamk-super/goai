import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const topCard = readFileSync(new URL("../../src/components/reports/v2/ReportTopCard.tsx", import.meta.url), "utf8");
const report = readFileSync(new URL("../../src/components/reports/v2/SupervisorReportV2.tsx", import.meta.url), "utf8");
const shell = readFileSync(new URL("../../src/components/reports/SupervisorLayeredReport.tsx", import.meta.url), "utf8");
const translations = readFileSync(new URL("../../src/lib/i18n.ts", import.meta.url), "utf8");

test("top card keeps stage, conditional comparison, confidence, coverage, and recommendation in order", () => {
  const stage = topCard.indexOf('t("Stage")');
  const comparison = topCard.indexOf('t("Compared with last time")');
  const confidence = topCard.indexOf('t("Confidence")');
  const coverage = topCard.indexOf('"Evidence coverage" : "Evidence completeness"');
  const recommendation = topCard.indexOf('t("Action recommendation")');
  assert.ok(stage > 0 && stage < comparison && comparison < confidence && confidence < coverage && coverage < recommendation);
  assert.match(topCard, /comparison\?\.status === "COMPARABLE"/);
  assert.match(topCard, /comparison\?\.status === "STANDARD_CHANGED"/);
  assert.doesNotMatch(topCard, /FIRST_EVALUATION|SAME_INPUT_RERUN/);
  assert.match(topCard, /status\(document\.top_card\.stage\)/);
});

test("the v2 report uses the product term and all four existing action recommendations", () => {
  assert.match(translations, /"Hit potential index": "爆款潜力指数"/);
  assert.doesNotMatch(report + topCard, /爆率|概率/);
  for (const recommendation of ["PROCEED", "VALIDATE_FURTHER", "ADJUST", "PAUSE"]) {
    assert.match(topCard, new RegExp(recommendation));
  }
});

test("critical claims expose inline citations and four specialist cards remain at the bottom", () => {
  assert.match(report, /document\.critical_issues/);
  assert.match(report, /<InlineCitation/);
  assert.match(report, /PENDING_VALIDATION/);
  assert.match(report, /<AgentReportCards/);
  assert.ok(report.indexOf("<AgentReportCards") > report.indexOf("<ReportActions"));
});

test("the full report renders every non-summary judgment, including user, product, investment and evidence gaps", () => {
  assert.match(report, /const detailedClaims = document\.claims\.filter/);
  assert.match(report, /claim\.claim_id !== document\.summary_claim_id/);
  assert.match(report, /t\("Detailed analysis"\)/);
  assert.match(report, /detailedClaims\.map\(renderClaim\)/);
});

test("the first-evaluation report explains the four index dimensions without inventing scores", () => {
  assert.match(report, /index-dimensions-title/);
  for (const dimension of ["user_value", "product_capability", "investment_potential", "evidence_quality"]) {
    assert.match(report, new RegExp(`t\\(\\"${dimension}\\"\\)`));
  }
  assert.match(report, /Only audited evidence can enter the index/);
});

test("Chinese reports translate claim labels and score dimensions instead of exposing enums", () => {
  assert.match(report, /t\(item\.dimension\)/);
  for (const [source, label] of [
    ["VERIFIED", "证据支持"],
    ["PENDING_VALIDATION", "待验证"],
    ["CRITICAL", "关键判断"],
    ["CONTEXT", "背景信息"],
    ["user_value", "用户价值"],
    ["product_capability", "产品能力"],
    ["investment_potential", "投资潜力"],
    ["evidence_quality", "证据质量"],
  ]) {
    assert.match(translations, new RegExp(`${source}:? \\"${label}\\"|\\"${source}\\": \\"${label}\\"`));
  }
});

test("legacy reports only branch to v2 for the explicit schema version", () => {
  assert.match(shell, /report_schema_version === "2\.0"/);
  assert.match(shell, /<SupervisorReportV2/);
  assert.match(shell, /formatStudentReport/);
});
