import assert from "node:assert/strict";
import test from "node:test";

import { runValidationDesign, loadSchemas } from "../src/index.mjs";
import { bindCapability, unbindAll } from "../src/tools/index.mjs";
import { validate as rawValidate } from "../src/validate.mjs";
import { createReferenceExecutor } from "./fixtures/reference-executor.mjs";
import { loadExample, runBound } from "./helpers/run.mjs";

const schemas = await loadSchemas();
const registry = {
  "evidence-card.schema.json": schemas.evidence,
  "persona.schema.json": schemas.persona,
  "validation-plan.schema.json": schemas.plan,
  [schemas.evidence.$id]: schemas.evidence,
  [schemas.persona.$id]: schemas.persona,
  [schemas.plan.$id]: schemas.plan,
};
const validate = (data, schema) => rawValidate(data, schema, registry);

async function runTransformed(input, transform) {
  unbindAll();
  for (const name of ["simulation_engine", "product_reader", "evidence_writer"]) {
    bindCapability(name, { kind: "role-acceptance-fixture" });
  }
  const reference = createReferenceExecutor();
  try {
    return await runValidationDesign(input, {
      schemas,
      executeStep: async (step, stepInput, context) =>
        transform(step, await reference(step, stepInput, context)),
    });
  } finally {
    unbindAll();
  }
}

test("role acceptance E2E: graduate-study material screening assistant", async () => {
  const result = await runBound(loadExample("input.example.json"));
  const output = result.structured_output;

  assert.equal(result.status, "completed");
  const completed = new Set(
    output.execution_log.filter((entry) => entry.outcome === "completed").map((entry) => entry.step_id),
  );
  for (const step of ["s1", "s2", "s3", "s4a", "s4b", "s5", "s6", "s7_synthesis"]) {
    assert.ok(completed.has(step), `${step} must complete`);
  }

  assert.deepEqual(new Set(output.personas.map((persona) => persona.archetype)), new Set(["high_need", "skeptic", "edge_case"]));
  assert.ok(output.personas.every((persona) => persona.eligible_for_scoring === true));
  const eligible = new Set(output.personas.filter((persona) => persona.eligible_for_scoring).map((persona) => persona.persona_id));
  for (const card of output.evidence_cards) {
    for (const personaId of card.applicability.persona_ids ?? []) assert.ok(eligible.has(personaId));
  }

  const matrix = output.simulated_findings.task_test_matrix;
  assert.equal(matrix.length, 9);
  assert.equal(new Set(matrix.map((row) => `${row.persona_id}::${row.task_key}`)).size, 9);

  const interviews = output.simulated_findings.simulated_interview;
  assert.equal(interviews.length, 3);
  assert.equal(new Set(interviews.map((entry) => entry.persona_id)).size, 3);
  assert.ok(interviews.every((entry) => entry.questions_raised.length > 0 && entry.complaints.length > 0));

  for (const key of ["demand_strength", "pain_severity", "willingness_to_pay"]) {
    const dimension = output.user_value_score.dimensions[key];
    assert.equal(dimension.max_tier, "E3", `${key} must be calibrated by the interview`);
    assert.ok(dimension.evidence_refs.includes("EV-USR-EXT-0003"));
  }
  assert.equal(output.user_value_score.dimensions.usage_frequency.max_tier, "E0", "supporting_claims and valid_for_dimensions intersect; H1/H2 do not authorize usage_frequency");
  assert.equal(output.user_value_score.dimensions.virality.max_tier, "E0", "a card scoped only to alternative_gap cannot support virality");

  assert.ok(output.critical_issue);
  assert.match(output.critical_issue.issue, /导出/);
  const actions = output.handoff.to_review_supervisor_agent.next_actions;
  assert.ok(actions.length >= 1 && actions.length <= 3);
  assert.equal(actions[0].owner_hint, "product_team_expert_agent");
  assert.ok(actions.slice(1).every((action) => action.owner_hint === "human"));

  const plans = new Map(output.validation_plans.map((plan) => [plan.hypothesis_id, plan]));
  for (const claim of output.evidence_level_summary.per_claim) {
    if (!claim.upgrade_plan_id) continue;
    const plan = plans.get(claim.claim_id);
    const upgrade = plan.evidence_upgrade.find((item) => item.claim_id === claim.claim_id);
    assert.equal(claim.target_tier, upgrade.to_tier);
  }
  assert.ok(output.validation_plans.every((plan) => plan.needs_human_review && plan.execution_owner === "human"));

  const calibration = output.handoff.to_evidence_calibration_agent;
  assert.ok(calibration.issued_evidence_cards.some((entry) => entry.reliability_level === "E2"));
  assert.ok(calibration.ingested_evidence.some((entry) => entry.reliability_level === "E3" && entry.origin === "caller_supplied"));
  assert.ok(output.handoff.to_review_supervisor_agent.key_real_evidence_refs.includes("EV-USR-EXT-0003"));

  const serialized = JSON.stringify(result);
  for (const bad of ["[object Object]", "undefined"]) assert.ok(!serialized.includes(bad));
  for (const decision of ["继续推进", "调整方向", "暂停投入"]) assert.ok(!serialized.includes(decision));

  assert.equal(validate(result, schemas.output).valid, true);
  for (const persona of output.personas) assert.equal(validate(persona, schemas.persona).valid, true);
  for (const plan of output.validation_plans) assert.equal(validate(plan, schemas.plan).valid, true);
  for (const card of output.evidence_cards) assert.equal(validate(card, schemas.evidence).valid, true);
});

test("role consistency: complete low-confidence Personas are not reported missing or allowed to score", async () => {
  const input = loadExample("input.example.json");
  input.existing_user_evidence = [];
  const result = await runBound(input, { lowConfidencePersonas: true });
  const output = result.structured_output;

  assert.notEqual(result.status, "completed");
  assert.equal(output.user_value_score.counted_weight, 0);
  assert.ok(output.personas.every((persona) => persona.eligible_for_scoring === false));
  const personaDiagnostics = output.missing_information.filter((entry) => entry.field.startsWith("personas."));
  assert.equal(personaDiagnostics.length, 3);
  assert.ok(personaDiagnostics.every((entry) => entry.state === "low_confidence"));
  assert.ok(personaDiagnostics.every((entry) => !entry.why_it_matters.includes("Persona is missing")));
});

test("role consistency: unrelated E3 cannot unlock dimensions or high confidence", async () => {
  const input = loadExample("simulation-only.example.json");
  input.existing_user_evidence = [{
    evidence_id: "EV-UNRELATED-E3",
    kind: "contract",
    tier: "E3",
    source: "aggregate contract fixture",
    timestamp: "2026-08-01",
    expiry: "2027-08-01",
    observation: "A vendor contract exists but says nothing about user value.",
    applies_to_product_version: "V1.0",
    applies_to_segment: "距考试 3 个月内的二战考研生",
    supporting_claims: ["LEGAL-1"],
    valid_for_dimensions: [],
  }];
  const result = await runBound(input);
  const output = result.structured_output;

  assert.equal(output.evidence_level_summary.has_real_user_evidence, false);
  assert.notEqual(output.evidence_confidence, "high");
  assert.ok(Object.values(output.user_value_score.dimensions).every((dimension) => dimension.max_tier !== "E3"));
  assert.ok(output.missing_information.some((entry) => entry.state === "insufficient_real_evidence"));
});

test("role consistency: incomplete Persona x task output is normalized and cannot complete", async () => {
  const result = await runTransformed(loadExample("input.example.json"), (step, outcome) =>
    step.id === "s4b" ? { ...outcome, taskTests: outcome.taskTests.slice(0, 3) } : outcome,
  );
  const matrix = result.structured_output.simulated_findings.task_test_matrix;
  assert.equal(matrix.length, 9);
  assert.ok(matrix.some((record) => record.result === "not_executed" && record.reason));
  assert.notEqual(result.status, "completed");
  assert.equal(result.failure_reason, "incomplete_task_matrix");
});

test("role consistency: missing per-Persona interview spends retries and cannot complete", async () => {
  const result = await runBound(loadExample("input.example.json"), { missingInterviewPersona: true });
  assert.notEqual(result.status, "completed");
  assert.equal(result.failure_reason, "simulation_invalid");
  assert.equal(result.structured_output.simulated_findings.realism_check.verdict, "fail");
  assert.equal(result.structured_output.simulated_findings.realism_check.retries_used, 2);
});

test("role consistency: model switching verdict cannot override deterministic forces", async () => {
  const result = await runTransformed(loadExample("input.example.json"), (step, outcome) =>
    step.id === "s3"
      ? {
          ...outcome,
          scenarios: outcome.scenarios.map((scenario) => ({
            ...scenario,
            switching_forces: { ...scenario.switching_forces, verdict: "will_switch" },
            flags: { ...scenario.flags, high_switching_friction: false },
          })),
        }
      : outcome,
  );
  const byId = new Map(result.structured_output.scenarios_and_alternatives.map((scenario) => [scenario.scenario_id, scenario]));
  assert.equal(byId.get("SC2").switching_forces.verdict, "will_not_switch");
  assert.equal(byId.get("SC3").flags.high_switching_friction, true);
  assert.equal(result.structured_output.flags.high_switching_friction, true);
});
