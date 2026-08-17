import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  HIT_PREDICTOR_STAGES,
  deriveProjectName,
  evaluationRouteForStage,
  nextPortraitQuestion,
  normalizeProductUrl,
  stageCodeFromProfile,
} from "../../src/lib/hit-predictor-intake.ts";

const landingSource = readFileSync(
  new URL("../../src/components/landing/PublicWheelLanding.tsx", import.meta.url),
  "utf8",
);

test("home intake exposes the five product stages and keeps the description primary", () => {
  assert.deepEqual(HIT_PREDICTOR_STAGES.map(item => item.code), ["IDEA", "PROTOTYPE", "DEMO_MVP", "USERS", "LIVE"]);
  assert.match(HIT_PREDICTOR_STAGES[0].detail, /初步预测/u);
  assert.match(HIT_PREDICTOR_STAGES[1].detail, /初步预测/u);
  assert.equal(deriveProjectName("为大学生提供可重复练习的 AI 模拟面试工具。希望验证续用。"), "为大学生提供可重复练习的 AI 模拟面试工具");
});

test("the generated project name is editable and the edited value is used for creation", () => {
  assert.match(landingSource, /value=\{projectName\}/u);
  assert.match(landingSource, /createProject\(session\.workspaceId, projectName\.trim\(\)\)/u);
});

test("product links accept bare domains and normalize them to HTTP or HTTPS URLs", () => {
  assert.equal(normalizeProductUrl("creatrades.com"), "https://creatrades.com/");
  assert.equal(normalizeProductUrl("www.creatrades.com/path?q=1"), "https://www.creatrades.com/path?q=1");
  assert.equal(normalizeProductUrl("http://localhost:3000/demo"), "http://localhost:3000/demo");
  assert.equal(normalizeProductUrl("https://app.creatrades.com"), "https://app.creatrades.com/");
  assert.throws(() => normalizeProductUrl("javascript:alert(1)"), /HTTP or HTTPS/u);
});

test("formal evaluation is admitted only from Demo or later", () => {
  assert.equal(evaluationRouteForStage("IDEA"), "INCUBATION");
  assert.equal(evaluationRouteForStage("PROTOTYPE"), "LIGHTWEIGHT_REVIEW");
  assert.equal(evaluationRouteForStage("DEMO_MVP"), "FORMAL_EVALUATION");
  assert.equal(evaluationRouteForStage("USERS"), "FORMAL_EVALUATION");
  assert.equal(evaluationRouteForStage("LIVE"), "FORMAL_EVALUATION");
  assert.equal(stageCodeFromProfile("已有 Demo / MVP"), "DEMO_MVP");
  assert.equal(stageCodeFromProfile("已上线运营"), "LIVE");
  assert.equal(
    stageCodeFromProfile(
      "截至 2026 年 8 月，已有可实际使用的 Web 端，核心链路已跑通，产品处于从功能可用走向稳定产品化的早期阶段；3 月材料所称计划 5 月上线是历史陈述，当前商业验证证据仍不足。",
    ),
    "DEMO_MVP",
  );
  assert.equal(stageCodeFromProfile("目前产品已经有可以实际使用的 Web 端，正在从功能可用向稳定产品化过渡。"), "DEMO_MVP");
  assert.equal(stageCodeFromProfile("已有可实际使用的真实 Web 端，模型调用、计费和素材链路已跑通。"), "DEMO_MVP");
  assert.equal(stageCodeFromProfile("尚未正式运营，计划 5 月上线"), null);
});

test("portrait supplement asks exactly one unresolved question at a time", () => {
  const questions = [
    { field: "target_user", question: "谁最需要它？", priority: 1 },
    { field: "differentiation", question: "为什么不用现有方案？", priority: 2 },
  ];
  assert.equal(nextPortraitQuestion(questions, {}).field, "target_user");
  assert.equal(nextPortraitQuestion(questions, { target_user: "准备校招的大学生" }).field, "differentiation");
  assert.equal(nextPortraitQuestion(questions, { target_user: "学生", differentiation: "可复验反馈" }), null);
});
