import assert from "node:assert/strict";
import { test } from "node:test";
import {
  buildCalibrationState,
  buildDimensionStates,
  buildSectorStates,
  advanceEvidenceNotch,
  notchFromEvidence,
  stageReadout,
  wheelMotionState,
} from "../../src/lib/wheel-state.ts";

const task = (ref: string, status: string, evidence = 0) => ({
  id: `id-${ref}-${status}`,
  stage_code: "DOMAIN_REVIEW",
  agent_identity_ref: ref,
  status,
  tool_allowlist: [] as string[],
  evidence_count: evidence,
});

test("sector fill mirrors real field state, never assumptions", () => {
  const sectors = buildSectorStates({
    one_line_value_claim: "一句话价值",
    problem: "痛点", core_features: "功能", inspectable_materials: "https://x",
    team: "三人", stage: "内测",
    target_user: "用户", payer: "付费者", validation_goal: "验证",
    region: "中国香港", timing: "Q3",
  });
  const product = sectors.find(s => s.key === "product");
  assert.deepEqual(
    sectors.map(s => [s.name, s.filled, s.total]),
    [["产品材料", 4, 4], ["团队能力", 2, 2], ["用户与经营", 3, 3], ["时间与地域", 2, 2]],
  );
  assert.equal(product?.filled, 4);
});

test("empty fields leave every sector unfilled", () => {
  const sectors = buildSectorStates({});
  assert.ok(sectors.every(s => s.filled === 0));
});

test("only the four judgment dimensions surface as wheel needles", () => {
  const dimensions = buildDimensionStates({ tasks: [] });
  assert.deepEqual(
    dimensions.map(d => d.code),
    ["PRODUCT_IMPLEMENTATION", "USER_USAGE", "BUSINESS_INVESTMENT", "GEO_POLICY_TREND"],
  );
  assert.ok(dimensions.every(d => d.evidence === 0));
  assert.deepEqual(dimensions.slice(0, 3).map(d => d.name), ["产品经理", "目标用户", "投资人"]);
});

test("generation v4 removes the historical geography worker from the wheel", () => {
  const dimensions = buildDimensionStates({ tasks: [] }, "v4");
  assert.deepEqual(
    dimensions.map(d => d.code),
    ["PRODUCT_IMPLEMENTATION", "USER_USAGE", "BUSINESS_INVESTMENT"],
  );
});

test("dimension evidence aggregates per specialist and honours status", () => {
  const dimensions = buildDimensionStates({
    tasks: [
      task("product-engineering@2.0", "SUCCEEDED", 3),
      task("business-investment@2.0", "NEEDS_INPUT", 1),
    ],
  });
  const product = dimensions.find(d => d.code === "PRODUCT_IMPLEMENTATION");
  const business = dimensions.find(d => d.code === "BUSINESS_INVESTMENT");
  assert.equal(product?.evidence, 3);
  assert.equal(product?.status, "SUCCEEDED");
  assert.equal(business?.status, "NEEDS_INPUT");
});

test("attention stops active dimension labels without hiding completed or user-input states", () => {
  const dimensions = buildDimensionStates({
    tasks: [
      task("product-engineering@2.0", "RUNNING", 2),
      task("user-evidence@2.0", "SUCCEEDED", 3),
      task("business-investment@2.0", "NEEDS_INPUT", 1),
    ],
  }, "v4", true);

  assert.equal(dimensions.find(d => d.code === "PRODUCT_IMPLEMENTATION")?.status, "NEEDS_ATTENTION");
  assert.equal(dimensions.find(d => d.code === "USER_USAGE")?.status, "SUCCEEDED");
  assert.equal(dimensions.find(d => d.code === "BUSINESS_INVESTMENT")?.status, "NEEDS_INPUT");
});

test("calibration is a state, not a fifth chat tab", () => {
  const calibrating = buildCalibrationState({ tasks: [task("evidence-auditor@2.0", "RUNNING", 2)] });
  assert.equal(calibrating.status, "CALIBRATING");
  assert.equal(calibrating.evidenceTotal, 2);
  const calibrated = buildCalibrationState({ tasks: [task("evidence-auditor@2.0", "SUCCEEDED", 5)] });
  assert.equal(calibrated.status, "CALIBRATED");
  const attention = buildCalibrationState({ tasks: [{ ...task("geo-policy-trend@2.0", "SUCCEEDED"), needs_human_review: true }] });
  assert.equal(attention.status, "ATTENTION");
});

test("the notch only advances when evidence exists", () => {
  assert.equal(notchFromEvidence(0), 0);
  assert.equal(notchFromEvidence(7), 7);
  assert.equal(notchFromEvidence(99), 32);
});

test("the durable notch never moves backward when a projection refresh is stale", () => {
  assert.equal(advanceEvidenceNotch(7, 3), 7);
  assert.equal(advanceEvidenceNotch(7, 9), 9);
  assert.equal(advanceEvidenceNotch(31, 99), 32);
});

test("wheel motion stops for human waits, attention, and completion", () => {
  assert.equal(wheelMotionState({ status: "RUNNING" }), "RUNNING");
  assert.equal(wheelMotionState({ status: "WAITING_FOR_USER" }), "PAUSED");
  assert.equal(wheelMotionState({ status: "WAITING_FOR_APPROVAL" }), "PAUSED");
  assert.equal(wheelMotionState({ status: "NEEDS_ATTENTION" }), "ATTENTION");
  assert.equal(wheelMotionState({ status: "COMPLETED" }), "COMPLETED");
});

test("stage readout surfaces durable run state only", () => {
  assert.equal(stageReadout(undefined), "资料收集");
  assert.equal(stageReadout({ status: "RUNNING", current_stage: "EVIDENCE_GATHERING" }), "EVIDENCE GATHERING");
  assert.equal(stageReadout({ status: "WAITING_FOR_USER", current_stage: null }), "WAITING FOR USER");
});
