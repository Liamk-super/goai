import assert from "node:assert/strict";
import test from "node:test";
import { buildBusinessInvestmentReport, selectBusinessInvestmentReport } from "../src/index.mjs";

const identity = {report_id: "10000000-0000-4000-8000-000000000001", run_id: "10000000-0000-4000-8000-000000000002", project_id: "10000000-0000-4000-8000-000000000003", product_version_id: "10000000-0000-4000-8000-000000000004", product_title: "真实产品"};
const source = {source_locator_id: "10000000-0000-4000-8000-000000000005", evidence_id: "10000000-0000-4000-8000-000000000006", source_kind: "PUBLIC_URL", canonical_url: "https://example.com/market", title: "市场资料", publisher: "示例机构", published_at: "2026-08-01T00:00:00Z", fetched_at: "2026-08-13T00:00:00Z", locator: {section: "market"}, region: "CN", independence_group: "example", content_sha256: "b".repeat(64)};

test("market claims need region and time provenance and projections share one SHA", () => {
  const input = {identity, observations: [{key: "market", section: "MARKET", text: "目标市场存在增长信号", sources: [source]}, {key: "competition", section: "COMPETITION", text: "竞争格局仍需验证"}], business_model: {revenue: "subscription"}, unit_economics: {gross_margin_range: "待验证"}, investment_gates: [{decision: "LIMIT", pass_when: "出现真实续费"}]};
  const report = buildBusinessInvestmentReport(input);
  assert.equal(report.claims[0].score_bearing, true);
  assert.equal(report.claims[1].status, "PENDING_VALIDATION");
  assert.equal(report.claims[1].citation_ids.length, 0);
  assert.equal(selectBusinessInvestmentReport(report, "summary").source_sha256, selectBusinessInvestmentReport(report, "full").source_sha256);
  assert.equal(JSON.stringify(report).includes("iframe"), false);
});

test("malformed source identities remain pending and never enter the source directory", () => {
  const input = {identity, observations: [{key: "bad-source", section: "MARKET", text: "错误来源不能支撑市场判断", sources: [{...source, evidence_id: "material:not-a-uuid", support_role: "SUPPORT"}]}], business_model: {}, unit_economics: {}, investment_gates: []};
  const report = buildBusinessInvestmentReport(input);

  assert.equal(report.claims[0].status, "PENDING_VALIDATION");
  assert.equal(report.claims[0].score_bearing, false);
  assert.deepEqual(report.citations, []);
  assert.deepEqual(report.source_directory, []);
});

test("background citations remain visible without upgrading a claim to verified", () => {
  const report = buildBusinessInvestmentReport({
    identity,
    observations: [{
      key: "context-only",
      section: "MARKET",
      text: "行业趋势只能提供背景，不能直接证明本产品需求",
      decision_relevance: "需要结合产品行为数据解释",
      sources: [{...source, support_role: "BACKGROUND"}],
    }],
    business_model: {},
    unit_economics: {},
    investment_gates: [],
  });

  assert.equal(report.claims[0].status, "PENDING_VALIDATION");
  assert.equal(report.claims[0].score_bearing, false);
  assert.equal(report.claims[0].decision_relevance, "IMPORTANT");
  assert.equal(report.citations[0].support_role, "BACKGROUND");
  assert.equal(report.source_directory.length, 1);
});
