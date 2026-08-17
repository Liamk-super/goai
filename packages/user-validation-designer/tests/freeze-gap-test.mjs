import assert from "node:assert/strict";
import test from "node:test";

import {
  buildEvidenceEffectLedger,
  deriveClaimEvidenceState,
  ingestExistingEvidence,
  ingestedContentHash,
  mapClaimApplicability,
} from "../src/evidence.mjs";
import { progressVerdict } from "../src/regression.mjs";
import { computeStateHash } from "../src/state-integrity.mjs";
import { assertAllowed } from "../src/tools/index.mjs";
import { scanInput } from "../src/pii-scan.mjs";
import { vetPlans } from "../src/validation-plans.mjs";
import { loadExample, runBound } from "./helpers/run.mjs";

const first = await runBound(loadExample("input.example.json"));
const frozen = first.structured_output;
const planByClaim = new Map(frozen.validation_plans.map((plan) => [plan.hypothesis_id, plan]));
const hypothesisById = new Map(frozen.user_hypotheses.map((claim) => [claim.hypothesis_id, claim]));

function tamperState(mutator) {
  const changed = structuredClone(frozen);
  mutator(changed);
  return changed;
}

function recheck(previous) {
  const input = loadExample("input.example.json");
  input.task_id += "-GAP-RECHECK";
  input.runtime.mode = "evidence_recheck";
  input.previous_structured_output = previous;
  input.previous_state_hash = frozen.run_manifest.state_hash;
  input.existing_user_evidence = [];
  return input;
}

async function assertTamperBlocked(mutator) {
  const result = await runBound(recheck(tamperState(mutator)));
  assert.equal(result.status, "blocked");
  assert.equal(result.failure_reason, "previous_state_integrity_mismatch");
}

function evidence(kind, sampleSize, relation = "supporting_claims") {
  return {
    evidence_id: `EV-GAP-${kind}-${sampleSize}`,
    kind,
    tier: "E3",
    source: "archive://controlled-research/run-1",
    timestamp: "2026-08-09T00:00:00Z",
    sample_size: sampleSize,
    observation: "Controlled aggregate research observation",
    applies_to_product_version: "V1.0",
    applies_to_persona_ids: ["P1"],
    valid_for_dimensions: kind === "survey" ? ["demand_strength"] : ["pain_severity"],
    [relation]: ["H1"],
  };
}

function deriveFor(kind, sampleSize, relation = "supporting_claims") {
  const hypothesis = { hypothesis_id: "H1", statement: "observable demand", claim_type: kind === "usability_test" ? "usability" : "demand", affected_dimensions: kind === "survey" ? ["demand_strength"] : ["pain_severity"], status: "open", current_evidence_level: "E2" };
  const ingested = ingestExistingEvidence([evidence(kind, sampleSize, relation)], { collected_at: "2026-08-09T00:00:00Z", product_version: "V1.0" });
  const mapped = mapClaimApplicability(ingested.records, [hypothesis]);
  const records = mapped.records.map((record) => ({ ...record, scope_valid: true }));
  const ledger = buildEvidenceEffectLedger(mapped.hypotheses, records);
  return deriveClaimEvidenceState(mapped.hypotheses, ledger, records)[0];
}

function planOptions(claimId) {
  const hypothesis = hypothesisById.get(claimId);
  return { claimTiers: { [claimId]: hypothesis.current_evidence_level }, hypotheses: [hypothesis], personaIds: new Set(["P1", "P2", "P3"]), constraints: null };
}

test("gap 01 tampered Persona changes state hash", () => assert.notEqual(computeStateHash(tamperState((s) => { s.personas[0].label += " tampered"; })), frozen.run_manifest.state_hash));
test("gap 02 tampered validation plan changes state hash", () => assert.notEqual(computeStateHash(tamperState((s) => { s.validation_plans[0].success_threshold.value += 1; })), frozen.run_manifest.state_hash));
test("gap 03 tampered segment changes state hash", () => assert.notEqual(computeStateHash(tamperState((s) => { s.target_user_definition.converged_segments[0].label += " tampered"; })), frozen.run_manifest.state_hash));
test("gap 04 tampered flags change state hash", () => assert.notEqual(computeStateHash(tamperState((s) => { s.flags.conflict = !s.flags.conflict; })), frozen.run_manifest.state_hash));
test("gap 05 sample_size changes ingested evidence hash", () => assert.notEqual(ingestedContentHash(evidence("interview", 1)), ingestedContentHash(evidence("interview", 100))));
test("gap 06 sample metadata tamper changes previous state hash", () => assert.notEqual(computeStateHash(tamperState((s) => { s.handoff.to_evidence_calibration_agent.ingested_evidence[0].sample_size += 100; })), frozen.run_manifest.state_hash));

test("gap 07 underpowered interview E3 does not validate claim", () => assert.equal(deriveFor("interview", 1).status, "open"));
test("gap 08 underpowered survey E3 does not settle claim", () => assert.equal(deriveFor("survey", 20).status, "open"));
test("gap 09 underpowered usability E3 does not settle claim", () => assert.equal(deriveFor("usability_test", 2).status, "open"));
test("gap 10 underpowered evidence does not retire its plan", () => {
  const h1 = first.structured_output.user_hypotheses.find((claim) => claim.hypothesis_id === "H1");
  assert.equal(h1.status, "open");
  assert.ok(first.structured_output.validation_plans.some((plan) => plan.hypothesis_id === "H1"));
});

test("gap 11 supporting_claims cannot bypass evidence-kind dimensions", () => {
  const record = ingestExistingEvidence([{ ...evidence("payment_record", 1), tier: "E5", valid_for_dimensions: ["virality"], supporting_claims: ["H1"] }], { collected_at: "2026-08-09T00:00:00Z", product_version: "V1.0" }).records[0];
  const mapped = mapClaimApplicability([record], [{ hypothesis_id: "H1", claim_type: "demand", affected_dimensions: ["demand_strength", "virality", "willingness_to_pay"] }]);
  assert.deepEqual(mapped.records[0].applicability.valid_for_dimensions, []);
  assert.deepEqual(mapped.records[0].supporting_claims, []);
});
test("gap 12 claim_type cannot carry illegal affected_dimensions", () => {
  const mapped = mapClaimApplicability([], [{ hypothesis_id: "H1", claim_type: "willingness_to_pay", affected_dimensions: ["willingness_to_pay", "virality"] }]);
  assert.deepEqual(mapped.hypotheses[0].affected_dimensions, ["willingness_to_pay"]);
  assert.ok(mapped.diagnostics.some((entry) => entry.code === "claim_type_dimension_mismatch"));
});
test("gap 13 unknown H404 relation is removed", () => {
  const record = ingestExistingEvidence([{ ...evidence("interview", 5), supporting_claims: ["H404"] }], { collected_at: "2026-08-09T00:00:00Z", product_version: "V1.0" }).records[0];
  const mapped = mapClaimApplicability([record], [{ hypothesis_id: "H1", claim_type: "demand", affected_dimensions: ["pain_severity"] }]);
  assert.deepEqual(mapped.records[0].supporting_claims, []);
  assert.ok(mapped.diagnostics.some((entry) => entry.code === "unknown_claim_relation"));
});

test("gap 14 WTP evidence cannot calibrate Persona goal pain or alternative", async () => {
  const input = loadExample("simulation-only.example.json");
  input.existing_user_evidence = ["P1", "P2", "P3"].map((personaId, index) => ({ ...evidence("payment_record", 1), evidence_id: `EV-WTP-${index}`, tier: "E5", applies_to_persona_ids: [personaId], valid_for_dimensions: ["willingness_to_pay"], supporting_claims: ["H2"] }));
  const result = await runBound(input, { lowConfidencePersonas: true });
  for (const persona of result.structured_output.personas) assert.deepEqual([persona.field_provenance.goal, persona.field_provenance.pains, persona.field_provenance.alternative], ["inference", "inference", "inference"]);
});
test("gap 15 low-confidence Persona stays low with unrelated E5", async () => {
  const input = loadExample("simulation-only.example.json");
  input.existing_user_evidence = ["P1", "P2", "P3"].map((personaId, index) => ({ ...evidence("payment_record", 1), evidence_id: `EV-WTP-L-${index}`, tier: "E5", applies_to_persona_ids: [personaId], valid_for_dimensions: ["willingness_to_pay"], supporting_claims: ["H2"] }));
  const result = await runBound(input, { lowConfidencePersonas: true });
  assert.ok(result.structured_output.personas.every((persona) => persona.confidence === "low" && persona.eligible_for_scoring === false));
});

test("gap 16 fake upstream Evidence ID cannot preserve functional attribution", async () => {
  const input = loadExample("simulation-only.example.json");
  input.upstream_product_handoff = { blocking_observations: [{ observation: "export fails", task_key: "export_weekly_plan", technical_attribution: "functional", evidence_refs: ["EV-FAKE-UPSTREAM"] }] };
  const result = await runBound(input);
  assert.ok(result.structured_output.simulated_findings.task_test_matrix.filter((task) => task.task_key === "export_weekly_plan").every((task) => task.cause_type !== "functional"));
  assert.ok(!result.structured_output.run_manifest.upstream_evidence_refs.includes("EV-FAKE-UPSTREAM"));
});
test("gap 17 valid Product Evidence ref may preserve functional attribution", async () => {
  const input = loadExample("simulation-only.example.json");
  input.evidence_refs = ["EV-PRODUCT-1"];
  input.upstream_product_handoff = { blocking_observations: [{ observation: "export fails", task_key: "export_weekly_plan", technical_attribution: "functional", evidence_refs: ["EV-PRODUCT-1"] }] };
  const result = await runBound(input);
  assert.ok(result.structured_output.simulated_findings.task_test_matrix.some((task) => task.task_key === "export_weekly_plan" && task.cause_type === "functional"));
});
test("gap 18 altered version-regression baseline hash blocks", async () => {
  const input = loadExample("regression.example.json");
  input.previous_validation_results.hypotheses[0].status = "open";
  const result = await runBound(input);
  assert.equal(result.failure_reason, "previous_state_integrity_mismatch");
});
test("gap 19 upgraded plus downgraded progress is mixed", () => assert.equal(progressVerdict({ baselineMatch: true, standardChanged: false, ledger: [{ transition: "upgraded" }, { transition: "downgraded" }] }), "mixed"));

test("gap 20 future-intent Mom Test question is not approved", () => {
  const plan = structuredClone(planByClaim.get("H1")); plan.tasks_or_questions[0].content = "Would you buy this next year?";
  assert.equal(vetPlans([plan], planOptions("H1")).plans.length, 0);
});
test("gap 21 numeric noise is not a valid threshold", () => {
  const plan = structuredClone(planByClaim.get("H1")); plan.success_threshold = { ...plan.success_threshold, metric_id: "M404", value: 2026, expression: "2026" };
  assert.equal(vetPlans([plan], planOptions("H1")).plans.length, 0);
});
test("gap 22 meaningless falsifiable numeric sentence is rejected", () => {
  const plan = structuredClone(planByClaim.get("H1")); plan.validation_target.falsifiable_statement = "If users are satisfied in 2026, the hypothesis fails";
  assert.equal(vetPlans([plan], planOptions("H1")).plans.length, 0);
});
test("gap 23 fake commitment label cannot create E4", () => {
  const plan = structuredClone(planByClaim.get("H2")); plan.success_metrics[0] = { ...plan.success_metrics[0], observable_event: "contact copy visible", commitment_type: null };
  assert.equal(vetPlans([plan], planOptions("H2")).plans.length, 0);
});
test("gap 24 real lead or reservation may create E4", () => assert.equal(vetPlans([structuredClone(planByClaim.get("H2"))], planOptions("H2")).plans.length, 1));
test("gap 25 real deposit or payment may create E5", () => {
  const plan = structuredClone(planByClaim.get("H2")); plan.target_evidence_level = "E5"; plan.evidence_upgrade[0].to_tier = "E5"; plan.success_metrics[0] = { ...plan.success_metrics[0], measurement_type: "money", observable_event: "deposit_paid", commitment_type: "deposit_paid" };
  assert.equal(vetPlans([plan], planOptions("H2")).plans.length, 1);
});

test("gap 26 unknown operation fails closed", () => assert.throws(() => assertAllowed("delete_user"), { code: "external_action_requires_approval" }));
test("gap 27 charge fails closed", () => assert.throws(() => assertAllowed("charge"), { code: "external_action_requires_approval" }));
test("gap 28 encoded free-text email is blocked", () => assert.equal(scanInput({ note: "alice%40example.com" }).clean, false));
test("gap 29 encoded free-text API key is blocked", () => assert.equal(scanInput({ note: "sk%2Dabcdefghijklmnop" }).clean, false));
test("gap 30 no-special-compliance note does not add collect_personal_data", async () => {
  const input = loadExample("simulation-only.example.json"); input.constraints ??= {}; input.constraints.compliance_notes = "无特别合规要求";
  const result = await runBound(input);
  assert.equal(result.structured_output.flags.compliance_concern, false);
  const noPiiPlan = result.structured_output.validation_plans.find((plan) => plan.hypothesis_id === "H1");
  assert.ok(noPiiPlan && !(noPiiPlan.external_actions_required ?? []).includes("collect_personal_data"));
});

test("gap E2E tampered segment plus matching E3 is blocked before scope validation", async () => {
  const previous = tamperState((state) => { state.target_user_definition.converged_segments[0].label = "tampered-segment"; });
  const input = recheck(previous);
  input.existing_user_evidence = [{ ...evidence("interview", 5), applies_to_persona_ids: [], applies_to_segment: "tampered-segment" }];
  const result = await runBound(input);
  assert.equal(result.failure_reason, "previous_state_integrity_mismatch");
  assert.equal(result.status, "blocked");
});

test("gap integrity attack set blocks Persona plan segment flag and sample tampering", async () => {
  await assertTamperBlocked((s) => { s.personas[0].label += " tampered"; });
  await assertTamperBlocked((s) => { s.validation_plans[0].success_threshold.value += 1; });
  await assertTamperBlocked((s) => { s.target_user_definition.converged_segments[0].label += " tampered"; });
  await assertTamperBlocked((s) => { s.flags.conflict = !s.flags.conflict; });
  await assertTamperBlocked((s) => { s.handoff.to_evidence_calibration_agent.ingested_evidence[0].sample_size += 1; });
});

test("gap provenance free-text source label is not promoted to tier_1", () => {
  const record = ingestExistingEvidence([{ ...evidence("interview", 5), source: "trust me" }], { collected_at: "2026-08-09T00:00:00Z", product_version: "V1.0" }).records[0];
  assert.equal(record.source_tier, "untraceable");
});
