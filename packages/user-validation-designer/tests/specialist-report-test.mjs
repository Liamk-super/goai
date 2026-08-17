import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { buildUserSpecialistReportV2, selectUserSpecialistReportV2 } from "../src/presentation.mjs";

const identity = {report_id: "20000000-0000-4000-8000-000000000001", run_id: "20000000-0000-4000-8000-000000000002", project_id: "20000000-0000-4000-8000-000000000003", product_version_id: "20000000-0000-4000-8000-000000000004", product_title: "真实产品"};
const source = {source_locator_id: "20000000-0000-4000-8000-000000000005", evidence_id: "20000000-0000-4000-8000-000000000006", source_kind: "INTERNAL_MATERIAL", title: "用户访谈", publisher: null, published_at: null, fetched_at: "2026-08-13T00:00:00Z", locator: {interview: 1}, region: "CN", independence_group: "interview-1", content_sha256: "c".repeat(64)};

test("user summary and full views select one immutable document", () => {
  const report = buildUserSpecialistReportV2({identity, findings: [{key: "retention", section: "RETENTION", text: "用户出现再次使用行为", sources: [source]}, {key: "payment", section: "PAYMENT", text: "付费意愿仍需验证"}], domainPayload: {segments: ["核心用户"], validation_plans: [{observable: "二次使用"}]}});
  assert.equal(report.product_title, "真实产品");
  assert.equal(report.claims[1].status, "PENDING_VALIDATION");
  assert.equal(selectUserSpecialistReportV2(report, "summary").source_sha256, selectUserSpecialistReportV2(report, "full").source_sha256);
  assert.equal(JSON.stringify(report).includes("iframe"), false);
});

test("malformed source identities are treated as pending validation", () => {
  const report = buildUserSpecialistReportV2({identity, findings: [{key: "bad-source", section: "RETENTION", text: "错误来源不能支撑用户判断", sources: [{...source, source_locator_id: "object/path", support_role: "SUPPORT"}]}]});

  assert.equal(report.claims[0].status, "PENDING_VALIDATION");
  assert.equal(report.claims[0].score_bearing, false);
  assert.deepEqual(report.citations, []);
  assert.deepEqual(report.source_directory, []);
});

test("free-form relevance explanations cannot escape the report contract enum", () => {
  const report = buildUserSpecialistReportV2({
    identity,
    findings: [{
      key: "pricing",
      section: "PRICING",
      text: "公开定价可作为付费验证锚点",
      decision_relevance: "这是商业化验证的关键判断",
      sources: [source],
    }],
  });

  assert.equal(report.claims[0].decision_relevance, "IMPORTANT");
});

test("background-only sources stay cited without upgrading a user claim", () => {
  const report = buildUserSpecialistReportV2({
    identity,
    findings: [{
      key: "market-context",
      section: "MARKET",
      text: "行业规模只能作为背景，不能直接证明本产品需求",
      sources: [{...source, support_role: "BACKGROUND"}],
    }],
  });

  assert.equal(report.claims[0].status, "PENDING_VALIDATION");
  assert.equal(report.claims[0].score_bearing, false);
  assert.equal(report.citations[0].support_role, "BACKGROUND");
});

test("the packaged report runner emits the canonical specialist document", () => {
  const input = {
    identity,
    findings: [{key: "retention", section: "RETENTION", text: "用户出现再次使用行为", sources: [source]}],
    domainPayload: {segments: ["核心用户"]},
    actions: [],
  };
  const completed = spawnSync(
    process.execPath,
    [fileURLToPath(new URL("../runner/report-cli.mjs", import.meta.url))],
    {input: JSON.stringify(input), encoding: "utf8"},
  );

  assert.equal(completed.status, 0, completed.stderr);
  const report = JSON.parse(completed.stdout);
  assert.equal(report.schema_version, "2.0");
  assert.equal(report.project_id, identity.project_id);
  assert.equal(report.product_version_id, identity.product_version_id);
  assert.equal(report.agent_code, "user-evidence");
  assert.equal("tenant_id" in report, false);
  assert.equal("task_id" in report, false);
  assert.equal("as_of" in report, false);
});
