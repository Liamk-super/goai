import assert from "node:assert/strict";
import { test } from "node:test";
import type { Report } from "../../src/lib/api-client.ts";
import { formatStudentReport, formatUserVisibleError } from "../../src/lib/user-report-formatter.ts";

const report: Report = {
  report_id: "report-1",
  run_id: "run-1",
  project_id: "project-1",
  decision_id: "decision-1",
  recommendation: "VALIDATE_FURTHER",
  standard_version: "1.0",
  dimension_grades: {},
  blocking_reasons: [],
  action_items: [],
  created_at: "2026-08-13T00:00:00Z",
  evidence_chain: [{
    report_id: "report-1", decision_id: "decision-1", finding_id: "finding-1", evidence_id: "evidence-1",
    object_key: "private", sha256: "a".repeat(64), source_type: "USER", trust_level: "E3",
  }],
  calibration_results: [
    { finding_id: "finding-1", decision: "ACCEPTED", reason: "ok", contract_version: "3", rule_ids: [], evidence_ids: [], score_components: {}, flags: [] },
  ],
  deterministic_score: { score: 61, coverage: 0.62, recommendation: "VALIDATE_FURTHER", dimension_scores: {}, caps_applied: [], missing_agents: [] },
  layered_report: {
    summary: "Three ACCEPTED audited findings support VALIDATE_FURTHER but more evidence is required.",
    actions: ["Run one targeted remediation", "Collect direct US small-seller usage and payment evidence"],
    largest_opportunity: "Strong PMF Signal among early users",
    largest_risk: "Insufficient audited findings for unit economics",
    coverage: 0.62,
    confidence: 0.5,
    information_gaps: ["user-evidence"],
    conflicts: [],
    cross_domain_analysis: [],
    citations: [],
    version_changes: {},
    decision_conflict: false,
    synthesis_status: "ACCEPTED",
  },
};

test("Simplified Chinese student reports never expose raw English paragraphs or internal enums", () => {
  const formatted = formatStudentReport(report, "zh-CN");
  const visible = JSON.stringify(formatted);
  for (const banned of ["VALIDATE_FURTHER", "ACCEPTED", "audited findings", "remediation", "unit economics"]) {
    assert.equal(visible.includes(banned), false, banned);
  }
  assert.equal(formatted.verdict, "需要继续验证");
  assert.equal(formatted.scoreLabel, "综合评审参考分 61 / 100");
  assert.ok(formatted.actions.every(item => /[\u3400-\u9fff]/u.test(item)));
  assert.ok(formatted.gaps.every(item => /[\u3400-\u9fff]/u.test(item)));
});

test("technical failures have bilingual summaries and preserve raw detail separately", () => {
  const raw = "RemoteProtocolError: peer closed connection without sending complete message body (incomplete chunked read)";
  const chinese = formatUserVisibleError(raw, "zh-CN");
  const english = formatUserVisibleError(raw, "en");

  assert.equal(chinese.summary, "上游分析服务提前中断了响应。本次预测已经停止，已完成结果和证据均会保留。");
  assert.equal(english.summary, "The upstream analysis service ended its response early. This prediction stopped, and completed results and evidence were preserved.");
  assert.equal(chinese.technicalDetail, raw);
  assert.equal(english.technicalDetail, raw);
});

test("ordinary localized messages do not create redundant technical details", () => {
  assert.deepEqual(formatUserVisibleError("报告暂时无法读取", "zh-CN"), { summary: "报告暂时无法读取" });
  assert.deepEqual(formatUserVisibleError("Report unavailable", "en"), { summary: "Report unavailable" });
});

test("an interrupted unsettled stream is explained without exposing the raw English error", () => {
  const raw = "stream client disconnected before settlement";
  assert.deepEqual(formatUserVisibleError(raw, "zh-CN"), {
    summary: "模型响应中断，用量尚待核对。预测已安全停止，不会自动重复提交，已有材料和进度均会保留。",
    technicalDetail: raw,
  });
  assert.equal(
    formatUserVisibleError(raw, "en").summary,
    "The model response was interrupted before usage could be settled. The prediction stopped without an automatic resubmission, and existing materials and progress were preserved.",
  );
});
