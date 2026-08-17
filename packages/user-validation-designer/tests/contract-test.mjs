/**
 * Contract tests for user-validation-designer V1.0.4.
 *
 * These exercise the REAL pipeline (src/index.mjs) through the same helper the
 * committed examples are generated with, so an example can never disagree with
 * an assertion here. Nothing asserts on a hand-written literal that the code
 * also hard-codes; every check goes through runValidationDesign or a rule
 * function with constructed input.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { loadExample, refreshRegressionHash, runBound, runUnbound } from "./helpers/run.mjs";
import { loadSchemas } from "../src/index.mjs";
import { validate } from "../src/validate.mjs";
import {
  DIMENSION_KEYS,
  WEIGHTS,
  applyCounting,
  scoreTotals,
  userValueJudgment,
  toOverallJudgment,
  checkPersonaSet,
  personaEligibility,
  checkRealism,
  resolveConflict,
} from "../src/rules.mjs";
import { clampIssuedCards, ingestExistingEvidence } from "../src/evidence.mjs";
import { productTasksHash } from "../src/product-tasks-hash.mjs";
import { validate as validatePlanModule } from "../src/validate.mjs";
import { vetPlans } from "../src/validation-plans.mjs";
import { checkThresholdIntegrity } from "../src/regression.mjs";

const schemas = await loadSchemas();
const registry = {
  "evidence-card.schema.json": schemas.evidence,
  "persona.schema.json": schemas.persona,
  "validation-plan.schema.json": schemas.plan,
  [schemas.evidence.$id]: schemas.evidence,
  [schemas.persona.$id]: schemas.persona,
  [schemas.plan.$id]: schemas.plan,
};

const outputExample = (name) =>
  JSON.parse(readFileSync(new URL(`../examples/${name}`, import.meta.url), "utf8"));

// --- 1. admission ----------------------------------------------------------

test("1: an unexecutable target user is blocked, with clarification questions", async () => {
  const result = await runBound(loadExample("broad-target-user.example.json"));

  assert.equal(result.status, "blocked");
  assert.equal(result.failure_reason, "target_user_too_broad");
  const definition = result.structured_output.target_user_definition;
  assert.equal(definition.admitted, false);
  assert.equal(definition.breadth_check.verdict, "too_broad");
  assert.ok(
    definition.clarification_questions.length > 0,
    "a blocked admission must say what to supply instead",
  );
  // Nothing downstream may be invented on a blocked run.
  assert.deepEqual(result.structured_output.personas, []);
  assert.deepEqual(result.structured_output.validation_plans, []);
});

// --- 2. unbound tools ------------------------------------------------------

test("2: with no capability bound nothing is fabricated", async () => {
  const result = await runUnbound(loadExample("input.example.json"));

  assert.equal(result.status, "blocked");
  assert.equal(result.failure_reason, "tool_unavailable");
  assert.deepEqual(result.structured_output.personas, []);
  assert.deepEqual(result.structured_output.simulated_findings.task_test_matrix, []);
  assert.deepEqual(result.structured_output.simulated_findings.first_experience, []);
  assert.deepEqual(result.structured_output.evidence_cards, []);

  const executed = result.structured_output.execution_log.filter((e) => e.outcome === "completed");
  const simulationSteps = executed.filter((e) => ["s2", "s3", "s4a", "s4b", "s5"].includes(e.step_id));
  assert.equal(simulationSteps.length, 0, "no simulation step may report completion unbound");
});

// --- 3. PII ----------------------------------------------------------------

test("3: PII in the envelope is blocked and the value is never echoed", async () => {
  const input = loadExample("input.example.json");
  const phone = "13812345678";
  input.target_users.raw_description += `，联系人手机号 ${phone}`;

  const result = await runBound(input);

  assert.equal(result.status, "blocked");
  assert.equal(result.failure_reason, "pii_in_input");
  assert.equal(result.needs_human_review, true);
  assert.equal(result.retryable, false, "re-running the same envelope cannot help");
  assert.ok(
    !JSON.stringify(result).includes(phone),
    "the serialized result must not contain the personal value anywhere",
  );
});

// --- 4/5. evidence tiers ---------------------------------------------------

test("4: a card this skill issues can never exceed E2, and the downgrade is recorded", () => {
  const forged = [
    {
      evidence_id: "EV-forged-1",
      evidence_type: "real_user_evidence",
      source: "simulation://s5/P1",
      source_tier: "tier_3",
      timestamp: "2026-08-09",
      reliability_level: "E5",
      supporting_claims: ["H1"],
      applicability: { product_version: "V1", scope: "s5", valid_for_dimensions: ["demand_strength"] },
      expiry: "unknown",
      content_hash: "a".repeat(64),
      observation: "claims a real purchase",
      fact_type: "fact",
    },
  ];

  const { cards, downgraded } = clampIssuedCards(forged);
  assert.equal(cards[0].reliability_level, "E2");
  assert.equal(downgraded.length, 1);
  assert.equal(downgraded[0].from_tier, "E5");
  assert.equal(downgraded[0].to_tier, "E2");
});

test("4b: every issued card in a real run sits at E2 or below", () => {
  for (const name of ["output.example.json", "simulation-only.output.example.json", "with-real-evidence.output.example.json"]) {
    for (const card of outputExample(name).structured_output.evidence_cards) {
      assert.ok(
        ["E0", "E1", "E2"].includes(card.reliability_level),
        `${name}: issued card ${card.evidence_id} is ${card.reliability_level}`,
      );
    }
  }
});

test("5: ingested E3+ keeps its tier and is not re-issued as this skill's own card", async () => {
  const { records } = ingestExistingEvidence(
    [
      {
        evidence_id: "EV-REAL-1",
        kind: "payment_record",
        tier: "E5",
        source: "billing export",
        timestamp: "2026-07-01",
        observation: "12 paid conversions",
      },
    ],
    { collected_at: "2026-08-09" },
  );

  assert.equal(records[0].reliability_level, "E5", "real evidence keeps its tier");
  assert.equal(records[0].origin, "caller_supplied");

  const result = outputExample("with-real-evidence.output.example.json");
  const summary = result.structured_output.evidence_level_summary;
  assert.equal(summary.has_real_user_evidence, true);
  assert.ok(summary.tier_distribution.E3 + summary.tier_distribution.E4 + summary.tier_distribution.E5 > 0);

  const issuedIds = result.structured_output.evidence_cards.map((c) => c.evidence_id);
  assert.ok(
    !issuedIds.includes("EV-USR-REAL-0001"),
    "a caller's real evidence must not reappear as an issued card",
  );
});

// --- 6/7. judgment ceilings ------------------------------------------------

function dimensionsAll(score, ref) {
  const dimensions = {};
  for (const key of DIMENSION_KEYS) dimensions[key] = { score, evidence_refs: [ref] };
  return dimensions;
}

test("6: without E3+ evidence a perfect simulated score is capped at medium/preliminary", () => {
  const cards = [{ evidence_id: "SIM", reliability_level: "E2" }];
  const counted = applyCounting(dimensionsAll(5, "SIM"), cards);
  const totals = scoreTotals(counted);

  assert.equal(totals.normalized_total, 100);

  const capped = userValueJudgment({
    normalized_total: totals.normalized_total,
    dimensions: counted,
    hasRealUserEvidence: false,
  });
  assert.equal(capped.judgment, "medium");
  assert.equal(capped.ceiling_applied, true);
  assert.equal(capped.preliminary, true);

  const uncapped = userValueJudgment({
    normalized_total: totals.normalized_total,
    dimensions: counted,
    hasRealUserEvidence: true,
  });
  assert.equal(uncapped.judgment, "strong", "the same score with real evidence may reach strong");
});

test("6b: the simulation-only example really is capped and marked preliminary", () => {
  const score = outputExample("simulation-only.output.example.json").structured_output.user_value_score;
  assert.equal(score.preliminary, true);
  assert.equal(outputExample("simulation-only.output.example.json").structured_output.evidence_level_summary.has_real_user_evidence, false);
  const judgment = outputExample("simulation-only.output.example.json").structured_output.user_value_judgment;
  assert.ok(["medium", "weak", "very_weak", "unverified"].includes(judgment), `got ${judgment}`);
});

test("7: three dimensions without user evidence force unverified", () => {
  const cards = [
    { evidence_id: "SIM", reliability_level: "E2" },
    { evidence_id: "SELF", reliability_level: "E1" },
  ];
  const dimensions = {};
  DIMENSION_KEYS.forEach((key, index) => {
    dimensions[key] = { score: 5, evidence_refs: [index < 3 ? "SELF" : "SIM"] };
  });

  const counted = applyCounting(dimensions, cards);
  const judgment = userValueJudgment({
    normalized_total: scoreTotals(counted).normalized_total,
    dimensions: counted,
    hasRealUserEvidence: true,
  });

  assert.equal(judgment.judgment, "unverified");
  assert.equal(toOverallJudgment("unverified"), "insufficient_evidence");
});

test("7b: C2 folding — uncounted dimensions leave the weight base, they do not score zero", () => {
  const cards = [
    { evidence_id: "SIM", reliability_level: "E2" },
    { evidence_id: "SELF", reliability_level: "E1" },
  ];
  const dimensions = {};
  DIMENSION_KEYS.forEach((key, index) => {
    dimensions[key] = { score: 4, evidence_refs: [index < 2 ? "SELF" : "SIM"] };
  });

  const counted = applyCounting(dimensions, cards);
  const totals = scoreTotals(counted);

  const expectedWeight = DIMENSION_KEYS.slice(2).reduce((sum, key) => sum + WEIGHTS[key], 0);
  assert.equal(totals.counted_weight, expectedWeight);
  assert.equal(totals.normalized_total, 80, "4/5 across counted dimensions rescales to 80");
  for (const key of DIMENSION_KEYS.slice(0, 2)) {
    assert.equal(counted[key].counted, false);
    assert.equal(counted[key].score, null, "an unevidenced dimension is null, never a low score");
    assert.equal(counted[key].cap_reason, "team_self_report_only");
  }
});

// --- 8. missing product tasks ---------------------------------------------

test("8: without product_tasks the task test is skipped, never invented", async () => {
  const result = await runBound(loadExample("no-product-task.example.json"));

  assert.equal(result.structured_output.simulated_findings.executed.task_test, false);
  assert.deepEqual(result.structured_output.simulated_findings.task_test_matrix, []);
  assert.equal(result.failure_reason, "missing_product_task");

  const skipped = result.structured_output.simulated_findings.skip_reasons.map((s) => s.unit);
  assert.ok(skipped.includes("s4b"));
  const missing = result.structured_output.missing_information.map((m) => m.field);
  assert.ok(missing.includes("product_tasks"));
});

// --- 9/10. persona discipline ---------------------------------------------

test("9: a persona missing required elements cannot support scoring", () => {
  const incomplete = { persona_id: "P9", label: "半成品", rejection_reasons: [] };
  const eligibility = personaEligibility(incomplete);
  assert.equal(eligibility.eligible, false);
  assert.ok(eligibility.missing.length > 0);
});

test("10: personas that differ only cosmetically fail the differentiation check", () => {
  const keys = {
    alternative_in_use: "Excel",
    budget_constraint: "50/月",
    skill_level: "intermediate",
    urgency: 4,
    risk_attitude: "averse",
  };
  const clones = ["P1", "P2", "P3"].map((id) => ({
    persona_id: id,
    archetype: id === "P1" ? "high_need" : id === "P2" ? "skeptic" : "edge_case",
    behavior_keys: { ...keys },
  }));

  const check = checkPersonaSet(clones);
  assert.equal(check.differentiation.verdict, "fail");
  assert.ok(check.differentiation.homogeneous_pairs.length > 0);
});

test("10b: the real run produces a differentiated persona set", () => {
  const check = outputExample("output.example.json").structured_output.persona_set_check;
  assert.equal(check.differentiation.verdict, "pass");
  assert.deepEqual(check.archetype_coverage, { high_need: true, skeptic: true, edge_case: true });
  assert.ok(check.count >= 3 && check.count <= 5);
});

// --- 11. realism -----------------------------------------------------------

test("11: a simulation with no complaint or hidden need is judged unrealistic", () => {
  assert.equal(checkRealism({ negativeFindings: 0, hiddenNeeds: 3, executed: true }).verdict, "fail");
  assert.equal(checkRealism({ negativeFindings: 2, hiddenNeeds: 0, executed: true }).verdict, "fail");
  assert.equal(checkRealism({ negativeFindings: 2, hiddenNeeds: 1, executed: true }).verdict, "pass");
  assert.equal(
    checkRealism({ executed: false }).verdict,
    "not_applicable",
    "a step that never ran is not the same as a broken simulation",
  );
});

test("11b: the real run passes realism with genuine negatives", () => {
  const realism = outputExample("output.example.json").structured_output.simulated_findings.realism_check;
  assert.equal(realism.verdict, "pass");
  assert.ok(realism.negative_findings_count > 0);
  assert.ok(realism.hidden_needs_count > 0);
});

// --- 12. hypothesis <-> plan cross reference -------------------------------

test("12: every open hypothesis is either planned or explicitly deferred", () => {
  for (const name of ["output.example.json", "with-real-evidence.output.example.json", "regression.output.example.json"]) {
    const output = outputExample(name).structured_output;
    const planned = new Set(output.validation_plans.map((p) => p.hypothesis_id));
    for (const hypothesis of output.user_hypotheses) {
      if (hypothesis.status !== "open") continue;
      const covered = planned.has(hypothesis.hypothesis_id) || hypothesis.linked_plan_ids.length > 0;
      const deferred = typeof hypothesis.deferred_reason === "string" && hypothesis.deferred_reason.length > 0;
      assert.ok(covered || deferred, `${name}: ${hypothesis.hypothesis_id} is an orphan`);
    }
  }
});

test("12b: each plan points back at a claim that exists", () => {
  const output = outputExample("output.example.json").structured_output;
  const claims = new Set(output.evidence_level_summary.per_claim.map((c) => c.claim_id));
  for (const plan of output.validation_plans) {
    assert.ok(claims.has(plan.hypothesis_id), `${plan.plan_id} targets an unknown claim`);
  }
});

// --- 13. evidence upgrade --------------------------------------------------

test("13: a plan that does not raise any evidence level is rejected", () => {
  const base = outputExample("output.example.json").structured_output.validation_plans[0];

  const noUpgrade = { ...structuredClone(base), plan_id: "VP-BAD", evidence_upgrade: [] };
  const backwards = {
    ...structuredClone(base),
    plan_id: "VP-BACK",
    evidence_upgrade: [
      { claim_id: base.hypothesis_id, claim: "x", from_tier: "E2", to_tier: "E2", upgrade_condition: "none" },
    ],
  };
  const claimTiers = { [base.hypothesis_id]: "E2" };
  const hypothesis = outputExample("output.example.json").structured_output.user_hypotheses.find((h) => h.hypothesis_id === base.hypothesis_id);

  const result = vetPlans([noUpgrade, backwards], { claimTiers, constraints: null, hypotheses: [hypothesis] });
  assert.equal(result.plans.length, 0);
  assert.equal(result.rejected.length, 2);
});

test("13b: a malformed evidence_upgrade is reported, not thrown", () => {
  const base = outputExample("output.example.json").structured_output.validation_plans[0];
  const malformed = { ...structuredClone(base), plan_id: "VP-OBJ", evidence_upgrade: { claim_id: "H1" } };
  const hypothesis = outputExample("output.example.json").structured_output.user_hypotheses.find((h) => h.hypothesis_id === base.hypothesis_id);
  const result = vetPlans([malformed], { claimTiers: { H1: "E2" }, constraints: null, hypotheses: [hypothesis] });
  assert.equal(result.plans.length, 0);
  assert.ok(result.rejected[0].problems.join(" ").includes("array"));
});

// --- 14. human review ------------------------------------------------------

test("14: any plan touching real users is pinned to human execution and review", () => {
  for (const name of ["output.example.json", "with-real-evidence.output.example.json"]) {
    const output = outputExample(name).structured_output;
    assert.ok(output.validation_plans.length > 0, `${name} should design at least one plan`);
    for (const plan of output.validation_plans) {
      assert.equal(plan.needs_human_review, true, `${plan.plan_id} must require review`);
      assert.equal(plan.execution_owner, "human", `${plan.plan_id} must be human-executed`);
      assert.equal(plan.target_participants.must_be_real_user, true);
    }
    assert.equal(outputExample(name).needs_human_review, true);
    assert.equal(output.flags.external_action_pending_approval, true);
  }
});

test("14b: a model cannot switch human review off", () => {
  const base = outputExample("output.example.json").structured_output.validation_plans[0];
  const sneaky = {
    ...structuredClone(base),
    plan_id: "VP-SNEAK",
    needs_human_review: false,
    execution_owner: "agent",
  };
  const claimTiers = { [base.hypothesis_id]: base.current_evidence_level };
  const hypothesis = outputExample("output.example.json").structured_output.user_hypotheses.find((h) => h.hypothesis_id === base.hypothesis_id);
  const { plans } = vetPlans([sneaky], { claimTiers, constraints: null, hypotheses: [hypothesis] });

  assert.equal(plans.length, 1);
  assert.equal(plans[0].needs_human_review, true);
  assert.equal(plans[0].execution_owner, "human");
});

// --- 15. conflict ----------------------------------------------------------

test("15: real evidence beats simulation and both sides are retained", () => {
  const resolution = resolveConflict(
    { ref: "SIM-1", tier: "E2" },
    { ref: "REAL-1", tier: "E4" },
  );
  assert.equal(resolution.resolution, "real_evidence_wins");
  assert.equal(resolution.winner_ref, "REAL-1");
  assert.equal(resolution.demoted_ref, "SIM-1");

  const peers = resolveConflict({ ref: "A", tier: "E2" }, { ref: "B", tier: "E2" });
  assert.equal(peers.resolution, "both_retained");
  assert.equal(peers.winner_ref, null, "equal tiers are never averaged into a winner");
});

// --- 16. output self-check -------------------------------------------------

test("16: every committed output example satisfies the output contract", () => {
  const names = [
    "output.example.json",
    "broad-target-user.output.example.json",
    "no-product-task.output.example.json",
    "simulation-only.output.example.json",
    "with-real-evidence.output.example.json",
    "regression.output.example.json",
    "tool-unavailable.output.example.json",
  ];
  for (const name of names) {
    const result = validate(outputExample(name), schemas.output, registry);
    assert.deepEqual(result.errors, [], `${name} violates output.schema.json`);
  }
});

test("16b: every committed input example satisfies the input contract", () => {
  for (const name of [
    "input.example.json",
    "broad-target-user.example.json",
    "no-product-task.example.json",
    "simulation-only.example.json",
    "with-real-evidence.example.json",
    "regression.example.json",
  ]) {
    const result = validate(loadExample(name), schemas.input, registry);
    assert.deepEqual(result.errors, [], `${name} violates input.schema.json`);
  }
});

test("16c: personas and plans validate against their own sub-schemas", () => {
  const output = outputExample("output.example.json").structured_output;
  for (const persona of output.personas) {
    assert.deepEqual(validatePlanModule(persona, schemas.persona, registry).errors, []);
  }
  for (const plan of output.validation_plans) {
    assert.deepEqual(validatePlanModule(plan, schemas.plan, registry).errors, []);
  }
});

// --- 17/18. regression discipline -----------------------------------------

test("17: the task hash is order-, whitespace- and max_steps-insensitive but content-sensitive", () => {
  const a = [
    { task_key: "t1", description: "上传 资料", expected_observable_outcome: "X", max_steps: 5 },
    { task_key: "t2", description: "导出", expected_observable_outcome: "Y" },
  ];
  const b = [
    { task_key: "t2", description: "导出", expected_observable_outcome: "Y", max_steps: 99 },
    { task_key: "t1", description: "  上传   资料 ", expected_observable_outcome: "X" },
  ];
  assert.equal(productTasksHash(a), productTasksHash(b));

  const changed = structuredClone(a);
  changed[0].description = "上传别的东西";
  assert.notEqual(productTasksHash(a), productTasksHash(changed));

  assert.equal(productTasksHash([]), null);
  assert.equal(productTasksHash(null), null);
  assert.match(productTasksHash(a), /^[a-f0-9]{64}$/);
});

test("17b: the regression run confirms it ran the same task baseline", () => {
  const comparison = outputExample("regression.output.example.json").structured_output.regression_comparison;
  assert.equal(comparison.product_tasks_hash_match, true);

  const declared = loadExample("regression.example.json").previous_validation_results.product_tasks_hash;
  const recomputed = productTasksHash(loadExample("regression.example.json").product_tasks);
  assert.equal(recomputed, declared, "the committed baseline hash must match the tasks it describes");
});

test("18: moving a success threshold without a reason fails the regression gate", () => {
  const plan = structuredClone(outputExample("output.example.json").structured_output.validation_plans[0]);
  plan.success_threshold.reused_from_previous_round = false;
  plan.success_threshold.change_reason = null;
  const previous = { hypotheses: [{ hypothesis_id: plan.hypothesis_id, success_threshold: "完全不同的旧阈值" }] };
  const check = checkThresholdIntegrity([plan], previous);
  assert.equal(check.violations.length, 1);
  assert.match(check.violations[0].problem, /without change_reason/);
});

test("18b: dropping a previously open hypothesis fails the round", async () => {
  const input = loadExample("regression.example.json");
  input.previous_validation_results.hypotheses.push({
    hypothesis_id: "H99",
    statement: "上一轮遗留但本轮被丢弃的假设",
    status: "open",
    evidence_level: "E2",
    success_threshold: "任意",
  });
  refreshRegressionHash(input);

  const result = await runBound(input, { dropInherited: true });

  assert.equal(result.status, "failed");
  assert.equal(result.failure_reason, "script_mismatch");
});

test("18c: a compliant regression round completes and carries its ledger", () => {
  const envelope = outputExample("regression.output.example.json");
  assert.equal(envelope.status, "completed");

  const comparison = envelope.structured_output.regression_comparison;
  assert.equal(comparison.standard_changed, false);
  assert.ok(comparison.hypothesis_ledger.length > 0);
  for (const entry of comparison.hypothesis_ledger) {
    assert.ok(
      ["upgraded", "downgraded", "unchanged", "newly_settled", "reopened"].includes(entry.transition),
      `unexpected transition ${entry.transition}`,
    );
  }
});

// --- happy path ------------------------------------------------------------

test("happy path: S1..S6 all execute and the envelope is completed", async () => {
  const result = await runBound(loadExample("input.example.json"));

  assert.equal(result.status, "completed");
  assert.equal(result.failure_reason, null);
  assert.deepEqual(validate(result, schemas.output, registry).errors, []);

  const executed = result.structured_output.execution_log
    .filter((entry) => entry.outcome === "completed")
    .map((entry) => entry.step_id);
  for (const unit of ["s1", "s2", "s3", "s4a", "s4b", "s5", "s6", "s7_synthesis"]) {
    assert.ok(executed.includes(unit), `${unit} did not complete`);
  }

  const output = result.structured_output;
  assert.ok(output.personas.length >= 3);
  assert.ok(output.jobs_to_be_done.length > 0);
  assert.ok(output.scenarios_and_alternatives.length > 0);
  assert.ok(output.simulated_findings.task_test_matrix.length > 0);
  assert.ok(output.user_hypotheses.length > 0);
  assert.ok(output.validation_plans.length > 0);
  assert.equal(output.simulated_findings.evidence_tier, "E2");
});

test("the skill never issues a project-level decision", () => {
  const forbidden = ["继续推进", "继续验证", "调整方向", "暂停投入"];
  for (const name of ["output.example.json", "with-real-evidence.output.example.json"]) {
    const serialized = JSON.stringify(outputExample(name));
    for (const phrase of forbidden) {
      assert.ok(!serialized.includes(phrase), `${name} contains project-level verdict "${phrase}"`);
    }
  }
});

test("the four-way handoff carries no project-level verdict to the supervisor", () => {
  const handoff = outputExample("output.example.json").structured_output.handoff;
  for (const key of [
    "to_product_team_expert_agent",
    "to_investment_business_agent",
    "to_evidence_calibration_agent",
    "to_review_supervisor_agent",
  ]) {
    assert.ok(handoff[key], `missing handoff slice ${key}`);
  }
  const supervisor = handoff.to_review_supervisor_agent;
  assert.ok(["strong", "medium", "weak", "insufficient_evidence"].includes(supervisor.overall_judgment));
  assert.equal(supervisor.next_actions[0].owner_hint, "product_team_expert_agent");
  for (const action of supervisor.next_actions.slice(1)) assert.equal(action.owner_hint, "human");
});
