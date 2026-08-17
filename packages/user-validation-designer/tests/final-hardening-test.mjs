import assert from "node:assert/strict";
import test from "node:test";

import { runValidationDesign, loadSchemas } from "../src/index.mjs";
import { ingestExistingEvidence } from "../src/evidence.mjs";
import { scanInput } from "../src/pii-scan.mjs";
import { applyCounting, emptyDimensions } from "../src/rules.mjs";
import { computeStateHash } from "../src/state-integrity.mjs";
import { validate as rawValidate } from "../src/validate.mjs";
import { createCapabilityContext } from "../src/tools/index.mjs";
import { createReferenceExecutor, REFERENCE_TIMESTAMP } from "./fixtures/reference-executor.mjs";
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
const validateOutput = (value) => rawValidate(value, schemas.output, registry);
const capabilityContext = () => createCapabilityContext({
  simulation_engine: { kind: "final-hardening-fixture" },
  product_reader: { kind: "final-hardening-fixture" },
  evidence_writer: { kind: "final-hardening-fixture" },
});

async function runTransformed(input, transform, opts = {}) {
  const reference = createReferenceExecutor(opts);
  return runValidationDesign(input, {
    now: REFERENCE_TIMESTAMP,
    capabilityContext: capabilityContext(),
    executeStep: async (step, stepInput, context) => transform(step, await reference(step, stepInput, context)),
  });
}

function makeRecheck(input, previous) {
  const next = structuredClone(input);
  next.task_id = `${input.task_id}-RECHECK`;
  next.runtime.mode = "evidence_recheck";
  next.previous_structured_output = previous;
  next.previous_state_hash = previous.run_manifest.state_hash;
  next.existing_user_evidence = [];
  return next;
}

function lateRegressionInput() {
  const input = loadExample("regression.example.json");
  const segment = input.target_users.segments[0];
  const dimensions = ["demand_strength", "usage_frequency", "pain_severity", "alternative_gap", "willingness_to_pay", "virality"];
  input.existing_user_evidence.push(...dimensions.map((dimension, index) => ({
    evidence_id: `EV-LATE-${index + 1}`,
    kind: "interview",
    tier: "E3",
    source: "controlled V2 interview",
    timestamp: "2026-09-06T00:00:00Z",
    sample_size: 5,
    observation: `Observed V2 behaviour for ${dimension}`,
    applies_to_product_version: "V2.0",
    applies_to_segment: segment,
    valid_for_dimensions: [dimension],
  })));
  return input;
}

test("1 regression late failure masks stale medium verdict", async () => {
  const input = lateRegressionInput();
  const control = await runBound(input);
  assert.equal(control.status, "completed");
  assert.equal(control.structured_output.user_value_judgment, "unverified");
  assert.ok(Object.values(control.structured_output.user_value_score.dimensions).some((dimension) => dimension.needs_rescore));
  const result = await runBound(input, { dropInherited: true });
  assert.equal(result.status, "failed");
  assert.equal(result.failure_reason, "script_mismatch");
  assert.equal(result.structured_output.user_value_judgment, "unverified");
  assert.equal(result.structured_output.overall_judgment, "insufficient_evidence");
  assert.equal(result.structured_output.evidence_confidence, "low");
  assert.equal(result.structured_output.user_value_score.counted_weight, 0);
  assert.deepEqual(result.structured_output.validation_plans, []);
  assert.equal(result.structured_output.handoff.to_review_supervisor_agent.user_value_judgment, "unverified");
});

test("2 regression failed summary contains no stale verdict or plan count", async () => {
  const result = await runBound(lateRegressionInput(), { dropInherited: true });
  assert.doesNotMatch(result.result_summary, /用户价值中|已设计\s*\d+\s*项/u);
  assert.match(result.result_summary, /未验证/u);
});

test("3 evidence_recheck with unchanged previous state succeeds without simulation", async () => {
  const input = loadExample("input.example.json");
  const first = await runBound(input);
  const recheck = makeRecheck(input, first.structured_output);
  let calls = 0;
  const result = await runValidationDesign(recheck, {
    now: REFERENCE_TIMESTAMP,
    capabilityContext: capabilityContext(),
    executeStep: async () => { calls += 1; return null; },
  });
  assert.equal(calls, 0);
  assert.equal(result.status, "completed");
  assert.equal(validateOutput(result).valid, true);
  assert.equal(result.structured_output.user_value_judgment, first.structured_output.user_value_judgment);
  assert.equal(result.structured_output.handoff.to_review_supervisor_agent.user_value_judgment, result.structured_output.user_value_judgment);
});

test("3b evidence_recheck accepts a trusted 1.0.4 baseline and emits 1.0.5", async () => {
  const input = loadExample("input.example.json");
  const first = await runBound(input);
  const legacy = structuredClone(first.structured_output);
  legacy.run_manifest.skill_version = "1.0.4";
  legacy.run_manifest.state_hash = computeStateHash(legacy);
  const recheck = makeRecheck(input, legacy);
  const result = await runValidationDesign(recheck, {
    now: REFERENCE_TIMESTAMP,
    capabilityContext: capabilityContext(),
    executeStep: async () => assert.fail("evidence_recheck must not rerun simulation"),
  });

  assert.equal(result.status, "completed");
  assert.equal(result.structured_output.run_manifest.skill_version, "1.0.5");
});

test("4 evidence_recheck restores old canonical evidence refs", async () => {
  const input = loadExample("input.example.json");
  const first = await runBound(input);
  const result = await runBound(makeRecheck(input, first.structured_output));
  const registryIds = new Set([
    ...result.structured_output.evidence_cards.map((card) => card.evidence_id),
    ...result.structured_output.handoff.to_evidence_calibration_agent.ingested_evidence_refs,
  ]);
  for (const dimension of Object.values(result.structured_output.user_value_score.dimensions)) {
    for (const ref of dimension.evidence_refs) assert.ok(registryIds.has(ref), ref);
  }
  assert.deepEqual(result.structured_output.evidence_cards, first.structured_output.evidence_cards);
});

test("5 evidence_recheck negative E5 contradicts and recalibrates old claim", async () => {
  const input = loadExample("input.example.json");
  const first = await runBound(input);
  const recheck = makeRecheck(input, first.structured_output);
  recheck.existing_user_evidence = [{
    evidence_id: "EV-NEG-PAY",
    kind: "payment_record",
    tier: "E5",
    source: "payment system aggregate",
    timestamp: "2026-08-09T01:00:00Z",
    sample_size: 100,
    observation: "100 eligible users saw paid offer; 0 completed payment",
    applies_to_product_version: "V1.0",
    applies_to_segment: input.target_users.segments[0],
    valid_for_dimensions: ["willingness_to_pay"],
    contradicts_claims: ["H2"],
  }];
  const result = await runBound(recheck);
  const claim = result.structured_output.user_hypotheses.find((entry) => entry.hypothesis_id === "H2");
  assert.equal(result.status, "completed");
  assert.equal(claim.status, "falsified");
  assert.equal(claim.current_evidence_level, "E5");
  assert.ok(claim.contradicting_refs.includes("EV-NEG-PAY"));
  const wtp = result.structured_output.user_value_score.dimensions.willingness_to_pay;
  assert.equal(wtp.score, null);
  assert.equal(wtp.needs_rescore, true);
  assert.ok(result.structured_output.evidence_effect_ledger.some((entry) => entry.evidence_id === "EV-NEG-PAY" && entry.claim_id === "H2" && entry.relation === "contradict"));
});

test("6 fake nonexistent E5 conflict ref cannot win", async () => {
  const result = await runTransformed(loadExample("input.example.json"), (step, outcome) => step.id === "s5" ? {
    ...outcome,
    conflictCandidates: [{
      conflict_id: "CF-FAKE",
      conflict_type: "simulation_vs_real",
      side_a: { ref: "EV-DOES-NOT-EXIST", tier: "E5", statement: "fake real evidence" },
      side_b: { ref: "EV-uvd-1", tier: "E2", statement: "simulation" },
    }],
  } : outcome);
  assert.equal(result.status, "failed");
  assert.equal(result.structured_output.conflicts.length, 0);
  assert.ok(result.structured_output.integrity_diagnostics.some((entry) => entry.ref === "CF-FAKE" && entry.code === "unknown_reference"));
});

test("7 conflict tier is derived from registry, not Worker", async () => {
  const result = await runTransformed(loadExample("input.example.json"), (step, outcome) => step.id === "s5" ? {
    ...outcome,
    conflictCandidates: [{
      conflict_id: "CF-FORGED-TIERS",
      conflict_type: "simulation_vs_real",
      side_a: { ref: "EV-uvd-4", tier: "E5", statement: "forged" },
      side_b: { ref: "EV-USR-EXT-0003", tier: "E0", statement: "forged" },
    }],
  } : outcome);
  const conflict = result.structured_output.conflicts[0];
  assert.equal(conflict.side_a.tier, "E2");
  assert.equal(conflict.side_b.tier, "E3");
  assert.equal(conflict.winner_ref, "EV-USR-EXT-0003");
});

test("8 six sample_size=1 E3 interviews cannot yield high confidence", async () => {
  const input = loadExample("input.example.json");
  const dimensions = ["demand_strength", "usage_frequency", "pain_severity", "alternative_gap", "willingness_to_pay", "virality"];
  input.existing_user_evidence = dimensions.map((dimension, index) => ({
    evidence_id: `EV-SMALL-${index + 1}`,
    kind: "interview", tier: "E3", source: "one-person interview", timestamp: "2026-08-09T00:00:00Z", sample_size: 1,
    observation: `One interview for ${dimension}`, applies_to_product_version: "V1.0", applies_to_segment: input.target_users.segments[0], valid_for_dimensions: [dimension],
  }));
  const result = await runBound(input);
  assert.notEqual(result.structured_output.evidence_confidence, "high");
  assert.ok(result.structured_output.handoff.to_evidence_calibration_agent.ingested_evidence.every((record) => record.sample_adequacy === "underpowered"));
});

test("9 all Personas ineligible plus unscoped simulation cannot score", async () => {
  const input = loadExample("simulation-only.example.json");
  const result = await runTransformed(input, (step, outcome) => ({
    ...outcome,
    evidence: (outcome.evidence ?? []).map((card) => ({ ...card, applicability: { ...card.applicability, persona_ids: [] } })),
  }), { lowConfidencePersonas: true });
  assert.ok(Object.values(result.structured_output.user_value_score.dimensions).every((dimension) => dimension.counted === false));
});

for (const [name, url, label] of [
  ["10 email in URL pathname blocked", "https://example.com/alice@example.com", "email_address"],
  ["11 mobile in URL pathname blocked", "https://example.com/13800138000", "cn_mobile_number"],
  ["12 token in URL pathname blocked", "https://example.com/sk-abcdefghijklmnop", "api_key"],
  ["13 encoded and double-encoded PII pathname blocked", "https://example.com/alice%2540example.com", "email_address"],
]) {
  test(name, () => {
    const result = scanInput({ product_profile: { url } });
    assert.equal(result.clean, false);
    assert.ok(result.findings.some((entry) => entry.label === label));
  });
}

test("14 public_comment cannot claim tier_1 source", () => {
  const result = ingestExistingEvidence([{ evidence_id: "EV-COMMENT", kind: "public_comment", tier: "E1", source_tier: "tier_1", source: "community", timestamp: "2026-08-09T00:00:00Z", observation: "anonymous public comment", applies_to_product_version: "V1.0" }], { collected_at: REFERENCE_TIMESTAMP, product_version: "V1.0" });
  assert.equal(result.records[0].source_tier, "tier_3");
  assert.ok(result.diagnostics.some((entry) => entry.code === "source_tier_clamped"));
});

test("15 major cognitive issue enters supervisor top risks", async () => {
  const result = await runTransformed(loadExample("input.example.json"), (step, outcome) => step.id === "s4b" ? {
    ...outcome,
    experienceIssues: [{ issue_id: "UX-MAJOR", description: "用户无法理解排序依据", severity: "major", cause_type: "cognitive", frequency_persona_count: 2, affected_personas: ["P2", "P3"], step_ref: "s4b", cognitive_break_point: true, evidence_refs: ["EV-uvd-3"] }],
  } : outcome);
  assert.ok(result.structured_output.top_user_problems.some((problem) => problem.related_issue_ids.includes("UX-MAJOR")));
  assert.ok(result.structured_output.handoff.to_review_supervisor_agent.top_risks.some((risk) => /无法理解排序依据/u.test(risk.question)));
});

test("16 worker business failure is not invalid_output_schema", async () => {
  const result = await runTransformed(loadExample("input.example.json"), (step, outcome) => step.id === "s3" ? { status: "failed", detail: "scenario worker could not complete", retryable: false } : outcome);
  assert.equal(result.status, "failed");
  assert.equal(result.failure_reason, "step_execution_failed");
  assert.notEqual(result.failure_reason, "invalid_output_schema");
});

test("17 malformed previous_structured_output is blocked", async () => {
  const input = loadExample("input.example.json");
  input.runtime.mode = "evidence_recheck";
  input.previous_structured_output = { run_manifest: {} };
  const result = await runBound(input);
  assert.equal(result.status, "blocked");
  assert.equal(result.failure_reason, "invalid_previous_state");
});

test("18 evidence_recheck project and version mismatch are blocked", async () => {
  const input = loadExample("input.example.json");
  const first = await runBound(input);
  for (const mutate of [
    (next) => { next.project_id = "OTHER-PROJECT"; },
    (next) => { next.product_version = "V9.0"; },
  ]) {
    const next = makeRecheck(input, first.structured_output);
    mutate(next);
    const result = await runBound(next);
    assert.equal(result.failure_reason, "invalid_previous_state");
  }
});

test("19 regression positive V1 to V2 example reports mixed when gains and regressions coexist", async () => {
  const result = await runBound(loadExample("regression.example.json"));
  assert.equal(result.status, "completed");
  assert.equal(result.structured_output.regression_comparison.progress_verdict, "mixed");
  assert.ok(result.structured_output.regression_comparison.task_comparison.some((entry) => entry.delta === "improved"));
  assert.ok(result.structured_output.regression_comparison.hypothesis_ledger.some((entry) => entry.transition === "upgraded"));
});

test("20 regression negative V1 evidence cannot score V2", async () => {
  const input = loadExample("regression.example.json");
  input.existing_user_evidence.push({ evidence_id: "EV-V1-STALE", kind: "interview", tier: "E3", source: "old V1 interview", timestamp: "2026-08-01T00:00:00Z", sample_size: 5, observation: "old evidence", applies_to_product_version: "V1.0", applies_to_segment: input.target_users.segments[0], valid_for_dimensions: ["virality"] });
  const result = await runBound(input);
  assert.ok(result.structured_output.integrity_diagnostics.some((entry) => entry.ref === "EV-V1-STALE" && entry.code === "product_version_mismatch"));
  assert.ok(!result.structured_output.user_value_score.dimensions.virality.evidence_refs.includes("EV-V1-STALE"));
});

test("21 Persona pain priority score is program-computed", async () => {
  const result = await runBound(loadExample("input.example.json"));
  for (const persona of result.structured_output.personas) {
    for (const pain of persona.pains) assert.equal(pain.priority_score, pain.frequency * pain.severity * pain.workaround_cost);
  }
});

test("22 politeness-only simulated evidence has zero scoring weight", () => {
  const dimensions = emptyDimensions();
  dimensions.demand_strength = { ...dimensions.demand_strength, score: 5, evidence_refs: ["EV-POLITE"] };
  const counted = applyCounting(dimensions, [{ evidence_id: "EV-POLITE", reliability_level: "E2", observation: "整体挺好的，很喜欢", applicability: { persona_ids: ["P1"], valid_for_dimensions: ["demand_strength"] } }], { eligiblePersonaIds: ["P1"] });
  assert.equal(counted.demand_strength.counted, false);
  assert.equal(counted.demand_strength.score, null);
});
