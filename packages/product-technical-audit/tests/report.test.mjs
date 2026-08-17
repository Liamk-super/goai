import assert from "node:assert/strict";
import test from "node:test";
import { buildProductTechnicalReport, selectProductTechnicalReport } from "../src/index.mjs";

const identity = {report_id: "00000000-0000-4000-8000-000000000001", run_id: "00000000-0000-4000-8000-000000000002", project_id: "00000000-0000-4000-8000-000000000003", product_version_id: "00000000-0000-4000-8000-000000000004", product_title: "真实产品"};
const source = {source_locator_id: "00000000-0000-4000-8000-000000000005", evidence_id: "00000000-0000-4000-8000-000000000006", source_kind: "INTERNAL_MATERIAL", title: "运行记录", publisher: null, published_at: null, fetched_at: "2026-08-13T00:00:00Z", locator: {page: 1}, region: null, independence_group: "runtime", content_sha256: "a".repeat(64)};

test("builds one canonical product report with stable selectors", () => {
  const input = {identity, stage: "MVP", observations: [{key: "core-flow", section: "CORE_FLOW", text: "核心流程可完成", decision_relevance: "CRITICAL", sources: [source]}, {key: "architecture", section: "RELIABILITY", text: "团队声称架构可扩展"}], stage_gates: [{gate: "核心流程", pass_when: "连续三次成功"}], core_flows: [{name: "创建项目"}], delivery_risks: ["关键依赖单点"], bus_factor: 1};
  const report = buildProductTechnicalReport(input);
  const again = buildProductTechnicalReport(input);
  assert.deepEqual(report, again);
  assert.equal(report.product_title, "真实产品");
  assert.equal(report.claims[1].status, "PENDING_VALIDATION");
  assert.equal(report.claims[1].score_bearing, false);
  assert.equal(selectProductTechnicalReport(report, "summary").source_sha256, selectProductTechnicalReport(report, "full").source_sha256);
  assert.equal(JSON.stringify(report).includes("iframe"), false);
});

test("downgrades malformed source identities instead of emitting an invalid verified claim", () => {
  const report = buildProductTechnicalReport({identity, stage: "MVP", observations: [{key: "bad-source", section: "CORE_FLOW", text: "错误来源不能参与评分", sources: [{...source, source_locator_id: "material-unit:not-a-uuid", support_role: "SUPPORT"}]}], stage_gates: [], core_flows: [], delivery_risks: [], bus_factor: 1});

  assert.equal(report.claims[0].status, "PENDING_VALIDATION");
  assert.equal(report.claims[0].score_bearing, false);
  assert.deepEqual(report.citations, []);
  assert.deepEqual(report.source_directory, []);
});

test("background-only sources cannot make a product claim verified", () => {
  const report = buildProductTechnicalReport({
    identity,
    stage: "MVP",
    observations: [{
      key: "market-context",
      section: "DEPENDENCIES",
      text: "行业材料不能直接证明产品链路可靠",
      decision_relevance: "需要运行证据",
      sources: [{...source, support_role: "BACKGROUND"}],
    }],
    stage_gates: [],
    core_flows: [],
    delivery_risks: [],
    bus_factor: 1,
  });

  assert.equal(report.claims[0].status, "PENDING_VALIDATION");
  assert.equal(report.claims[0].score_bearing, false);
  assert.equal(report.claims[0].decision_relevance, "IMPORTANT");
  assert.equal(report.citations[0].support_role, "BACKGROUND");
});
