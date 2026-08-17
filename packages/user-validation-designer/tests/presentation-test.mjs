import assert from "node:assert/strict";
import test from "node:test";

import { runValidationDesign } from "../src/index.mjs";
import {
  renderHumanReport,
  renderHumanReportHtml,
  renderSummaryReport,
  renderSummaryReportHtml,
  renderFullReport,
  renderFullReportHtml,
  visibleReportHasInternalTokens,
  HUMAN_REPORT_LIMITS,
} from "../src/presentation.mjs";
import { createCapabilityContext, unbindAll } from "../src/tools/index.mjs";
import { createReferenceExecutor, REFERENCE_TIMESTAMP } from "./fixtures/reference-executor.mjs";
import { loadExample, runBound } from "./helpers/run.mjs";
import { OFFERPILOT_GOLDEN } from "./fixtures/offerpilot-presentation.mjs";

test("admitted runs always expose summary and full human reports", async () => {
  const result = await runBound(loadExample("with-real-evidence.example.json"));
  const so = result.structured_output;
  for (const key of ["summary_report", "summary_report_html", "full_report", "full_report_html"]) {
    assert.ok(so[key], `${key} missing`);
    assert.equal(visibleReportHasInternalTokens(so[key]), false, `${key} leaked internal tokens`);
  }
  assert.equal(so.human_report, so.summary_report);
  assert.equal(so.human_report_html, so.summary_report_html);
});

test("summary report keeps five-section 3-30 second structure", async () => {
  const result = await runBound(loadExample("with-real-evidence.example.json"));
  const report = result.structured_output.summary_report;

  assert.ok(report.length <= HUMAN_REPORT_LIMITS.max_visible_chars);
  assert.match(report, /## 1\. 一眼看懂/u);
  assert.match(report, /核心用户/u);
  assert.match(report, /可争取/u);
  assert.match(report, /暂不优先/u);
  assert.match(report, /为什么会用/u);
  assert.match(report, /为什么不用/u);
  assert.match(report, /最大问题/u);
  assert.match(report, /## 2\. 当前最关键的问题/u);
  assert.match(report, /## 3\. 下一版先改什么/u);
  assert.match(report, /## 4\. 为什么这么判断/u);
  assert.match(report, /## 5\. 下一步怎么验证/u);
  assert.ok((report.match(/^##\s+/gmu) ?? []).length <= HUMAN_REPORT_LIMITS.max_main_sections);
  assert.doesNotMatch(report, /Persona 详细|用户假设总览|交接摘要|团队六大疑问/u);
});

test("first-screen target labels prefer behavior and scenario over grade labels", () => {
  const report = renderSummaryReport(OFFERPILOT_GOLDEN);
  const firstSection = report.split("## 2.")[0];
  assert.match(firstSection, /收到或等待面试通知/u);
  assert.match(firstSection, /ChatGPT/u);
  assert.doesNotMatch(firstSection, /大[一二三四]|研[一二三]|研究生|毕业生/u);
});

test("summary HTML is standalone concise output with no deep-analysis accordion", () => {
  const html = renderSummaryReportHtml(OFFERPILOT_GOLDEN);
  assert.ok(html);
  assert.equal(visibleReportHasInternalTokens(html), false);
  assert.match(html, /用户验证精简版/u);
  assert.match(html, /3–5 秒：先看客户与结论/u);
  assert.match(html, /当前最关键的问题/u);
  assert.match(html, /下一版先改什么/u);
  assert.match(html, /同次运行会同时生成《用户验证完备版》/u);
  assert.doesNotMatch(html, /<details|完整验证方案|用户价值评分与依据/u);
});

test("summary HTML uses distinct audience colors and one integrated decision strip", () => {
  const html = renderSummaryReportHtml(OFFERPILOT_GOLDEN);
  assert.match(html, /audience-card audience-core/u);
  assert.match(html, /audience-card audience-consider/u);
  assert.match(html, /audience-card audience-later/u);
  assert.equal((html.match(/class="decision-strip"/gu) ?? []).length, 1);
  assert.equal((html.match(/class="decision-item /gu) ?? []).length, 3);
});

test("full report is separate, complete and competition-material friendly", () => {
  const md = renderFullReport(OFFERPILOT_GOLDEN);
  const html = renderFullReportHtml(OFFERPILOT_GOLDEN);
  for (const artifact of [md, html]) {
    assert.ok(artifact);
    assert.equal(visibleReportHasInternalTokens(artifact), false);
    assert.match(artifact, /核心结论与目标用户/u);
    assert.match(artifact, /用户画像与使用场景|目标用户分群与判断依据/u);
    assert.match(artifact, /关键证据/u);
    assert.match(artifact, /当前最关键的用户问题/u);
    assert.match(artifact, /用户价值评分/u);
    assert.match(artifact, /产品改进优先级/u);
    assert.match(artifact, /完整用户验证执行方案/u);
    assert.match(artifact, /仍缺什么信息与使用边界/u);
    assert.match(artifact, /产品后台数据/u);
    assert.match(artifact, /真实用户访谈/u);
    assert.match(artifact, /秋招冲刺期、近期有面试的学生/u);
    assert.match(artifact, /真实复用观察/u);
    assert.match(artifact, /流失用户访谈/u);
    assert.doesNotMatch(artifact, /persona_id|plan_id|evidence_id|Agent handoff|交接摘要/u);
  }
});

test("summary and full reports agree on load-bearing verdicts", () => {
  const summary = renderSummaryReport(OFFERPILOT_GOLDEN);
  const full = renderFullReport(OFFERPILOT_GOLDEN);
  for (const token of ["用户需求：偏弱", "收到或等待面试通知", "产品还没有证明自己比现有替代方案明显更好", "反馈过于通用", "个性化追问还不够深"]) {
    assert.ok(summary.includes(token), `summary missing ${token}`);
    assert.ok(full.includes(token), `full missing ${token}`);
  }
});

test("human report does not surface state-machine or evidence-code language", async () => {
  const result = await runBound(loadExample("input.example.json"));
  const report = result.structured_output.human_report;
  for (const token of [
    "task_id", "project_id", "evidence_id", "persona_id", "hypothesis_id", "claim_id", "plan_id",
    "fact_type", "cap_reason", "run_manifest", "execution_log", "handoff", "integrity_diagnostics",
    "E0", "E1", "E2", "E3", "E4", "E5",
  ]) {
    assert.ok(!report.includes(token), `report leaked ${token}`);
  }
});

test("specific experience issues from an unexecuted S4 path are quarantined", async () => {
  const input = loadExample("no-product-task.example.json");
  input.product_profile.url = null;
  input.product_profile.experience_report_ref = null;
  input.existing_user_evidence = [];
  input.runtime.allowed_tools = ["simulation_engine", "evidence_writer"];

  const reference = createReferenceExecutor();
  const capabilityContext = createCapabilityContext({
    simulation_engine: { kind: "test-fixture" },
    evidence_writer: { kind: "test-fixture" },
  });

  unbindAll();
  try {
    const result = await runValidationDesign(input, {
      now: REFERENCE_TIMESTAMP,
      capabilityContext,
      executeStep: async (step, stepInput, context) => {
        const outcome = await reference(step, stepInput, context);
        if (step.id === "s5") {
          return {
            ...outcome,
            experienceIssues: [
              {
                issue_id: "FAKE-SAFARI",
                description: "Safari 偶尔无法使用麦克风",
                severity: "major",
                frequency_persona_count: 1,
                cause_type: "functional",
                affected_personas: ["P1"],
                step_ref: "麦克风",
                cognitive_break_point: false,
                evidence_refs: ["EV-NOT-OBSERVED"],
              },
            ],
          };
        }
        return outcome;
      },
    });

    assert.equal(result.structured_output.simulated_findings.executed.first_experience, false);
    assert.equal(result.structured_output.simulated_findings.executed.task_test, false);
    assert.ok(!result.structured_output.simulated_findings.experience_issues.some((issue) => issue.issue_id === "FAKE-SAFARI"));
    assert.ok(!result.structured_output.handoff.to_product_team_expert_agent.experience_issues.some((issue) => issue.issue_id === "FAKE-SAFARI"));
    assert.ok(!String(result.structured_output.human_report).includes("Safari"));
    assert.ok(!String(result.structured_output.human_report_html).includes("Safari"));
    assert.ok(result.structured_output.integrity_diagnostics.some((entry) => entry.code === "unobserved_experience_issue_removed"));
  } finally {
    unbindAll();
  }
});

test("presentation uses 30-day reuse wording unless source explicitly supplies standard D30", () => {
  const structured = minimalStructured();
  structured.top_user_problems = [{
    problem_id: "P",
    question: "D30 留存 37.5%，需要优化",
    why_it_matters: "影响复用",
    blocks_which_judgment: "user value judgment",
    rank: 1,
  }];
  const report = renderHumanReport({
    input: { product_profile: { name: "OfferPilot" } },
    structured,
    ingestedEvidence: [{ kind: "retention_data", observation: "168 人完成首次，63 人在 30 天内第二次使用" }],
  });
  assert.ok(report.includes("30 天内复用"));
  assert.ok(!report.includes("D30 留存"));
});

test("zero payment is presented as unverified monetization, not automatic rejection", () => {
  const structured = minimalStructured();
  const report = renderHumanReport({
    input: { product_profile: { name: "OfferPilot" } },
    structured,
    ingestedEvidence: [{ kind: "payment_record", observation: "真实付费 0 人" }],
  });
  assert.match(report, /付费仍需通过正式收费入口继续验证/u);
  assert.doesNotMatch(report, /最强负面|拒绝付费|付费失败/u);
});

test("OfferPilot golden report is scannable, product-facing, and keeps the load-bearing evidence", () => {
  const report = renderHumanReport(OFFERPILOT_GOLDEN);
  assert.ok(report.length <= HUMAN_REPORT_LIMITS.max_visible_chars);
  assert.equal(visibleReportHasInternalTokens(report), false);
  assert.match(report, /核心用户/u);
  assert.match(report, /ChatGPT/u);
  assert.match(report, /产品还没有证明自己比现有替代方案明显更好/u);
  assert.match(report, /反馈过于通用/u);
  assert.match(report, /个性化追问还不够深/u);
  assert.match(report, /等待过程缺少明确反馈/u);
  assert.match(report, /231/u);
  assert.match(report, /168/u);
  assert.match(report, /63/u);
  assert.match(report, /让报告引用本次回答/u);
  assert.match(report, /让每轮追问都引用具体经历或目标要求/u);
  assert.match(report, /付费意愿 \| 待验证/u);
  assert.doesNotMatch(report, /D30 留存/u);
  assert.doesNotMatch(report, /Safari|麦克风|弱网|上传失败/u);
});

function minimalStructured() {
  return {
    target_user_definition: { admitted: true, converged_segments: [] },
    personas: [],
    scenarios_and_alternatives: [],
    user_value_judgment: "weak",
    evidence_confidence: "medium",
    evidence_level_summary: { has_real_user_evidence: true },
    user_value_score: { dimensions: {} },
    simulated_findings: {
      experience_issues: [],
      insights: [],
    },
    top_user_problems: [],
    validation_plans: [],
    missing_information: [],
  };
}

