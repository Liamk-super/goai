import assert from "node:assert/strict";
import test from "node:test";

import { checkTargetUserBreadth, scanForExternalActionRequests } from "../src/admission.mjs";
import { ingestExistingEvidence, mapClaimApplicability } from "../src/evidence.mjs";
import { runValidationDesign, loadSchemas } from "../src/index.mjs";
import { checkHypothesisIdentity } from "../src/regression.mjs";
import { computeStateHash } from "../src/state-integrity.mjs";
import { createCapabilityContext } from "../src/tools/index.mjs";
import { vetPlans } from "../src/validation-plans.mjs";
import { createReferenceExecutor, REFERENCE_TIMESTAMP } from "./fixtures/reference-executor.mjs";
import { loadExample, runBound } from "./helpers/run.mjs";

const schemas = await loadSchemas();
const capabilities = () => createCapabilityContext({
  simulation_engine: { kind: "freeze-audit-fixture" },
  product_reader: { kind: "freeze-audit-fixture" },
  evidence_writer: { kind: "freeze-audit-fixture" },
});

async function runMutated(input, transform, opts = {}) {
  const reference = createReferenceExecutor(opts);
  return runValidationDesign(input, {
    schemas,
    now: REFERENCE_TIMESTAMP,
    capabilityContext: capabilities(),
    executeStep: async (step, stepInput, context) => transform(step, await reference(step, stepInput, context)),
  });
}

function recheckInput(base, previous, evidence = []) {
  const next = structuredClone(base);
  next.task_id = `${base.task_id}-FREEZE-RECHECK`;
  next.runtime.mode = "evidence_recheck";
  next.previous_structured_output = structuredClone(previous);
  next.previous_state_hash = previous.run_manifest.state_hash;
  next.existing_user_evidence = evidence;
  return next;
}

function realEvidence(overrides = {}) {
  return {
    evidence_id: "EV-FREEZE-REAL-1",
    kind: "interview",
    tier: "E3",
    source: "controlled aggregate interview archive",
    timestamp: "2026-08-09T01:00:00Z",
    expiry: "2027-08-09",
    sample_size: 6,
    observation: "Six eligible users described the last time this problem occurred.",
    applies_to_product_version: "V1.0",
    applies_to_segment: "距考试 3 个月内的二战考研生",
    supporting_claims: ["H1"],
    valid_for_dimensions: ["demand_strength", "pain_severity"],
    ...overrides,
  };
}

const baselineRun = await runBound(loadExample("simulation-only.example.json"));
const baselinePlans = new Map(baselineRun.structured_output.validation_plans.map((plan) => [plan.hypothesis_id, plan]));
const baselineHypotheses = baselineRun.structured_output.user_hypotheses;
const planOptions = (hypotheses = baselineHypotheses, constraints = null) => ({
  hypotheses,
  claimTiers: Object.fromEntries(hypotheses.map((claim) => [claim.hypothesis_id, claim.current_evidence_level])),
  personaIds: new Set(["P1", "P2", "P3"]),
  constraints,
});

test("01 missing product_tasks skips S4b but S5 and S6 still execute", async () => {
  const result = await runBound(loadExample("no-product-task.example.json"));
  assert.equal(result.failure_reason, "missing_product_task");
  assert.ok(result.structured_output.execution_log.some((entry) => entry.step_id === "s4b" && entry.outcome === "not_executable"));
  assert.ok(result.structured_output.execution_log.some((entry) => entry.step_id === "s5" && entry.outcome === "completed"));
  assert.ok(result.structured_output.execution_log.some((entry) => entry.step_id === "s6" && ["completed", "completed_with_rejections"].includes(entry.outcome)));
  assert.ok(result.structured_output.user_hypotheses.length > 0);
  assert.ok(result.structured_output.validation_plans.length + result.structured_output.deferred_validations.length > 0);
});

test("02 no product surface preserves demand research and planning", async () => {
  const input = loadExample("input.example.json");
  input.product_profile.url = null;
  input.product_profile.experience_report_ref = null;
  const result = await runBound(input);
  assert.ok(result.structured_output.execution_log.some((entry) => entry.step_id === "s4a" && entry.outcome === "not_executable"));
  assert.ok(result.structured_output.user_hypotheses.length > 0);
  assert.ok(result.structured_output.validation_plans.length > 0);
});

test("03 all-assumption Persona is programmatically low-confidence", async () => {
  const result = await runMutated(loadExample("simulation-only.example.json"), (step, outcome) => step.id !== "s2" ? outcome : ({
    ...outcome,
    personas: outcome.personas.map((persona) => ({ ...persona, confidence: "high", eligible_for_scoring: true, field_provenance: { goal: "assumption", pains: "assumption", alternative: "assumption", value_threshold: "assumption" } })),
  }));
  assert.ok(result.structured_output.personas.every((persona) => persona.confidence === "low" && persona.eligible_for_scoring === false));
});

test("04 model cannot self-declare Persona high confidence", async () => {
  const result = await runBound(loadExample("simulation-only.example.json"), { lowConfidencePersonas: true });
  assert.ok(result.structured_output.personas.every((persona) => persona.confidence === "low"));
});

test("05 wrong-segment E3 cannot calibrate Persona", async () => {
  const input = loadExample("simulation-only.example.json");
  input.existing_user_evidence = [realEvidence({ applies_to_segment: "unrelated segment", applies_to_persona_ids: ["P1", "P2", "P3"] })];
  const result = await runBound(input, { lowConfidencePersonas: true });
  assert.ok(result.structured_output.personas.every((persona) => persona.calibrated_by_real_evidence.length === 0));
});

test("06 borderline target caps evidence confidence at medium", async () => {
  const input = loadExample("input.example.json");
  input.target_users = { raw_description: "学生", segments: ["学生"] };
  input.existing_user_evidence = input.existing_user_evidence.map((entry) => ({ ...entry, applies_to_segment: "学生" }));
  const result = await runBound(input);
  assert.notEqual(result.structured_output.evidence_confidence, "high");
});

test("07 model-forged E5 fact validated hypothesis is normalized", async () => {
  const result = await runMutated(loadExample("simulation-only.example.json"), (step, outcome) => step.id !== "s5" ? outcome : ({ ...outcome, hypotheses: outcome.hypotheses.map((claim) => claim.hypothesis_id === "H1" ? { ...claim, current_evidence_level: "E5", fact_type: "fact", status: "validated" } : claim) }));
  const claim = result.structured_output.user_hypotheses.find((entry) => entry.hypothesis_id === "H1");
  assert.equal(claim.current_evidence_level, "E2");
  assert.equal(claim.status, "open");
  assert.notEqual(claim.fact_type, "fact");
});

test("08 model-forged Persona fact is normalized", async () => {
  const result = await runMutated(loadExample("simulation-only.example.json"), (step, outcome) => step.id !== "s2" ? outcome : ({ ...outcome, personas: outcome.personas.map((persona) => ({ ...persona, pains: persona.pains.map((pain) => ({ ...pain, fact_type: "fact" })) })) }));
  assert.ok(result.structured_output.personas.flatMap((persona) => persona.pains).every((pain) => pain.fact_type !== "fact"));
});

test("09 first-validation E5 contradiction changes H2", async () => {
  const input = loadExample("simulation-only.example.json");
  input.existing_user_evidence = [realEvidence({ evidence_id: "EV-PAY-0-100", kind: "payment_record", tier: "E5", observation: "0 of 100 eligible users paid", supporting_claims: [], contradicts_claims: ["H2"], valid_for_dimensions: ["willingness_to_pay"] })];
  const result = await runBound(input);
  const claim = result.structured_output.user_hypotheses.find((entry) => entry.hypothesis_id === "H2");
  assert.equal(claim.status, "falsified");
  assert.ok(claim.contradicting_refs.includes("EV-PAY-0-100"));
});

test("10 zero-of-100 payment cannot leave WTP at five", async () => {
  const input = loadExample("simulation-only.example.json");
  input.existing_user_evidence = [realEvidence({ evidence_id: "EV-PAY-ZERO", kind: "payment_record", tier: "E5", observation: "0/100 completed payment", supporting_claims: [], contradicts_claims: ["H2"], valid_for_dimensions: ["willingness_to_pay"] })];
  const result = await runBound(input);
  assert.notEqual(result.structured_output.user_value_score.dimensions.willingness_to_pay.score, 5);
});

test("11 real contradiction never uses generic score-minus-two", async () => {
  const input = loadExample("simulation-only.example.json");
  input.existing_user_evidence = [realEvidence({ evidence_id: "EV-PAY-NO-MAGIC", kind: "payment_record", tier: "E5", supporting_claims: [], contradicts_claims: ["H2"], valid_for_dimensions: ["willingness_to_pay"] })];
  const dimension = (await runBound(input)).structured_output.user_value_score.dimensions.willingness_to_pay;
  assert.equal(dimension.score, null);
  assert.equal(dimension.needs_rescore, true);
});

test("12 payment evidence cannot validate all six dimensions", () => {
  const ingested = ingestExistingEvidence([realEvidence({ kind: "payment_record", tier: "E5", valid_for_dimensions: ["demand_strength", "usage_frequency", "pain_severity", "alternative_gap", "willingness_to_pay", "virality"] })], { collected_at: REFERENCE_TIMESTAMP, product_version: "V1.0" });
  assert.deepEqual(ingested.records[0].applicability.valid_for_dimensions, ["willingness_to_pay"]);
});

test("13 wrong evidence kind and claim dimension are rejected", () => {
  const ingested = ingestExistingEvidence([realEvidence({ kind: "payment_record", tier: "E5", supporting_claims: ["H1"], valid_for_dimensions: ["demand_strength"] })], { collected_at: REFERENCE_TIMESTAMP, product_version: "V1.0" });
  const mapped = mapClaimApplicability(ingested.records, [{ hypothesis_id: "H1", claim_type: "demand", affected_dimensions: ["demand_strength"] }]);
  assert.deepEqual(mapped.records[0].supporting_claims, []);
  assert.ok(mapped.diagnostics.some((entry) => entry.code === "evidence_kind_claim_mismatch"));
});

test("14 version_stable old payment cannot score current version", () => {
  const result = ingestExistingEvidence([realEvidence({ kind: "payment_record", tier: "E5", applies_to_product_version: "V0.9", version_stable: true, stable_reason: "claimed stable" })], { collected_at: REFERENCE_TIMESTAMP, product_version: "V1.0" });
  assert.equal(result.records[0].integrity_valid, false);
});

test("15 recheck support E3 retires fulfilled old plan", async () => {
  const base = loadExample("simulation-only.example.json");
  const first = await runBound(base);
  const result = await runBound(recheckInput(base, first.structured_output, [realEvidence()]));
  assert.equal(result.status, "completed");
  assert.ok(!result.structured_output.validation_plans.some((plan) => plan.hypothesis_id === "H1"));
});

test("16 recheck contradiction does not fail on stale old plan", async () => {
  const base = loadExample("simulation-only.example.json");
  const first = await runBound(base);
  const negative = realEvidence({ evidence_id: "EV-RECHECK-NEG", supporting_claims: [], contradicts_claims: ["H1"] });
  const result = await runBound(recheckInput(base, first.structured_output, [negative]));
  assert.notEqual(result.failure_reason, "unsupported_validation_method");
  assert.equal(result.structured_output.user_hypotheses.find((claim) => claim.hypothesis_id === "H1").status, "falsified");
});

test("17 real-vs-real support and contradiction remains unresolved", async () => {
  const input = loadExample("simulation-only.example.json");
  input.existing_user_evidence = [realEvidence({ evidence_id: "EV-REAL-SUPPORT" }), realEvidence({ evidence_id: "EV-REAL-CONTRADICT", supporting_claims: [], contradicts_claims: ["H1"] })];
  const result = await runBound(input);
  const conflict = result.structured_output.conflicts.find((entry) => entry.conflict_type === "real_vs_real");
  assert.ok(conflict);
  assert.equal(conflict.resolution, "unresolved");
});

test("18 tampered previous state is blocked", async () => {
  const base = loadExample("input.example.json");
  const first = await runBound(base);
  const next = recheckInput(base, first.structured_output);
  next.previous_structured_output.user_value_score.dimensions.willingness_to_pay.basis = "TAMPERED SCORE";
  const result = await runBound(next);
  assert.equal(result.status, "blocked");
  assert.equal(result.failure_reason, "previous_state_integrity_mismatch");
});

test("19 same evidence id with changed content is rejected", async () => {
  const base = loadExample("input.example.json");
  const first = await runBound(base);
  const changed = { ...structuredClone(base.existing_user_evidence.find((entry) => entry.evidence_id === "EV-USR-EXT-0003")), kind: "payment_record", tier: "E5", observation: "changed content" };
  const result = await runBound(recheckInput(base, first.structured_output, [changed]));
  assert.ok(result.structured_output.integrity_diagnostics.some((entry) => entry.code === "evidence_id_content_mismatch"));
  assert.notEqual(result.status, "completed");
});

test("20 ingested evidence content hash is deterministic", () => {
  const options = { collected_at: REFERENCE_TIMESTAMP, product_version: "V1.0" };
  const a = ingestExistingEvidence([realEvidence()], options).records[0].content_hash;
  const b = ingestExistingEvidence([realEvidence()], options).records[0].content_hash;
  assert.equal(a, b);
  assert.match(a, /^[a-f0-9]{64}$/u);
});

test("21 fake alternative evidence reference is detected", async () => {
  const result = await runMutated(loadExample("input.example.json"), (step, outcome) => step.id !== "s3" ? outcome : ({ ...outcome, scenarios: outcome.scenarios.map((scenario, index) => index ? scenario : ({ ...scenario, alternatives: scenario.alternatives.map((alternative, i) => i ? alternative : ({ ...alternative, evidence_refs: ["EV-FAKE-ALT"] })) })) }));
  assert.ok(result.structured_output.integrity_diagnostics.some((entry) => entry.code === "unknown_reference"));
  assert.notEqual(result.status, "completed");
});

test("22 fake task-test evidence reference is detected", async () => {
  const result = await runMutated(loadExample("input.example.json"), (step, outcome) => step.id !== "s4b" ? outcome : ({ ...outcome, taskTests: outcome.taskTests.map((record, index) => index ? record : ({ ...record, evidence_refs: ["EV-FAKE-TASK"] })) }));
  assert.ok(result.structured_output.integrity_diagnostics.some((entry) => entry.code === "unknown_reference"));
});

test("23 all public evidence references resolve", async () => {
  const output = (await runBound(loadExample("input.example.json"))).structured_output;
  const registry = new Set([...output.evidence_cards.map((card) => card.evidence_id), ...output.handoff.to_evidence_calibration_agent.ingested_evidence_refs]);
  const missing = [];
  const visit = (value, path = "output") => {
    if (Array.isArray(value)) return value.forEach((item, index) => visit(item, `${path}[${index}]`));
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      if (["evidence_refs", "supporting_refs", "contradicting_refs", "calibrated_by_real_evidence"].includes(key) && Array.isArray(child)) for (const ref of child) if (!registry.has(ref)) missing.push(`${path}.${key}:${ref}`);
      visit(child, `${path}.${key}`);
    }
  };
  visit(output);
  assert.deepEqual(missing, []);
});

test("24 price click alone cannot validate WTP", () => {
  const plan = structuredClone(baselinePlans.get("H2"));
  plan.success_metrics = [{ metric: "price button click rate", metric_type: "behavioral", measurable: true }];
  plan.evidence_upgrade[0].upgrade_condition = "price button clicks reach 10%";
  assert.equal(vetPlans([plan], planOptions()).plans.length, 0);
});

test("25 contact or lead commitment may support E4 behavior", () => {
  const plan = structuredClone(baselinePlans.get("H2"));
  plan.success_metrics = [{ metric_id: "M2", metric: "qualified contact reservations", metric_type: "commitment", measurement_type: "commitment", observable_event: "reservation_created", commitment_type: "reservation_created", measurable: true }];
  plan.evidence_upgrade[0].upgrade_condition = "qualified contact reservation submitted by at least 10%";
  assert.equal(vetPlans([plan], planOptions()).plans.length, 1);
});

test("26 actual deposit or payment may reach E5", () => {
  const plan = structuredClone(baselinePlans.get("H2"));
  plan.target_evidence_level = "E5";
  plan.evidence_upgrade[0].to_tier = "E5";
  plan.evidence_upgrade[0].upgrade_condition = "actual deposit or payment received from at least 10%";
  plan.success_metrics = [{ metric_id: "M2", metric: "paid deposits", metric_type: "monetary", measurement_type: "money", observable_event: "deposit_paid", commitment_type: "deposit_paid", measurable: true }];
  assert.equal(vetPlans([plan], planOptions()).plans.length, 1);
});

test("27 nonnumeric threshold is rejected", () => {
  const plan = structuredClone(baselinePlans.get("H1"));
  plan.success_threshold.expression = "至少用户认为有帮助";
  plan.success_threshold.value = Number.NaN;
  assert.equal(vetPlans([plan], planOptions()).plans.length, 0);
});

test("28 numeric and saturation thresholds are accepted", () => {
  const numeric = structuredClone(baselinePlans.get("H1"));
  const saturation = structuredClone(numeric);
  saturation.plan_id = "VP-SATURATION";
  saturation.success_threshold.expression = "连续 2–3 场无新主题";
  assert.equal(vetPlans([numeric, saturation], planOptions()).plans.length, 2);
});

test("29 future purchase Mom Test question is rejected", () => {
  const plan = structuredClone(baselinePlans.get("H1"));
  plan.tasks_or_questions[0].content = "你会不会购买这个产品？";
  assert.equal(vetPlans([plan], planOptions()).plans.length, 0);
});

test("30 concrete past behavior question is accepted", () => {
  const plan = structuredClone(baselinePlans.get("H1"));
  plan.tasks_or_questions[0].content = "你上一次遇到这个问题是什么时候？当时怎么解决？";
  assert.equal(vetPlans([plan], planOptions()).plans.length, 1);
});

test("31 preference-only statement is not falsifiable", () => {
  const plan = structuredClone(baselinePlans.get("H1"));
  plan.validation_target.falsifiable_statement = "用户喜欢这个产品";
  assert.equal(vetPlans([plan], planOptions()).plans.length, 0);
});

test("32 twenty-week high-cost plan is not executable", () => {
  const plan = structuredClone(baselinePlans.get("H1"));
  plan.duration.weeks = 20;
  plan.estimated_cost.money_cny = 999999;
  const result = vetPlans([plan], planOptions(baselineHypotheses, { time_budget_weeks: 2, money_budget_cny: 1000, team_capacity_person_days: 10, recruitable_channels: plan.recruitment_criteria.channels }));
  assert.equal(result.plans.length, 0);
  assert.equal(result.deferred.length, 1);
});

test("33 person-days above capacity is infeasible", () => {
  const plan = structuredClone(baselinePlans.get("H1"));
  plan.estimated_cost.person_days = 999;
  const result = vetPlans([plan], planOptions(baselineHypotheses, { time_budget_weeks: 20, money_budget_cny: 999999, team_capacity_person_days: 5, recruitable_channels: plan.recruitment_criteria.channels }));
  assert.equal(result.plans.length, 0);
  assert.match(result.deferred[0].reason, /person-days/u);
});

test("34 unavailable recruitment channel is infeasible", () => {
  const plan = structuredClone(baselinePlans.get("H1"));
  plan.recruitment_criteria.channels = ["unavailable mystery channel"];
  const result = vetPlans([plan], planOptions(baselineHypotheses, { time_budget_weeks: 20, money_budget_cny: 999999, team_capacity_person_days: 999, recruitable_channels: ["school lab"] }));
  assert.equal(result.plans.length, 0);
  assert.match(result.deferred[0].reason, /channel/u);
});

test("35 negated universal target is not blocked", () => {
  const result = checkTargetUserBreadth({ raw_description: "不是所有人，只针对每周整理考研资料超过2小时、距考试3个月内的二战考生", segments: ["二战考生"] });
  assert.notEqual(result.verdict, "too_broad");
});

test("36 actual universal target remains blocked", () => {
  assert.equal(checkTargetUserBreadth({ raw_description: "所有人", segments: ["所有人"] }).verdict, "too_broad");
});

test("37 design-only interview request is allowed", () => {
  const result = scanForExternalActionRequests({ validation_goal: { objective: "请设计访谈方案，不实际联系用户", focus_questions: [] } });
  assert.equal(result.clean, true);
});

test("38 execute-now contact request requires approval", () => {
  const result = scanForExternalActionRequests({ validation_goal: { objective: "现在联系这些用户并发送访谈邀请", focus_questions: [] } });
  assert.equal(result.clean, false);
  assert.ok(result.findings.some((entry) => entry.label === "contact_user"));
});

test("39 same hypothesis id with changed statement fails identity", () => {
  const result = checkHypothesisIdentity({ hypotheses: [{ hypothesis_id: "H3", statement: "用户能独立完成导出本周清单" }] }, [{ hypothesis_id: "H3", statement: "完全不同的新问题" }]);
  assert.equal(result.violations.length, 1);
});

test("40 explicit reframe is standard-changed and incomparable", () => {
  const result = checkHypothesisIdentity({ hypotheses: [{ hypothesis_id: "H3", statement: "old statement" }] }, [{ hypothesis_id: "H3", statement: "new statement", standard_changed: true, reframe_reason: "scope redefined" }]);
  assert.equal(result.violations.length, 0);
  assert.equal(result.reframes.length, 1);
});

test("41 claim tier cannot exceed canonical evidence tier", async () => {
  const result = await runMutated(loadExample("simulation-only.example.json"), (step, outcome) => step.id !== "s5" ? outcome : ({ ...outcome, hypotheses: outcome.hypotheses.map((claim) => ({ ...claim, current_evidence_level: "E5" })) }));
  assert.ok(result.structured_output.user_hypotheses.every((claim) => ["E0", "E1", "E2"].includes(claim.current_evidence_level)));
});

test("42 settled claim cannot target a lower tier", async () => {
  const output = (await runBound(loadExample("input.example.json"))).structured_output;
  for (const claim of output.evidence_level_summary.per_claim.filter((entry) => !entry.upgradable)) assert.equal(claim.target_tier, claim.current_tier);
});

test("43 regression ledger includes contradiction references", async () => {
  const input = loadExample("regression.example.json");
  input.existing_user_evidence.push(realEvidence({ evidence_id: "EV-LEDGER-CONTRADICT", applies_to_product_version: "V2.0", applies_to_segment: input.target_users.segments[0], supporting_claims: [], contradicts_claims: ["H1"] }));
  const result = await runBound(input);
  const entry = result.structured_output.regression_comparison.hypothesis_ledger.find((item) => item.hypothesis_id === "H1");
  assert.ok(entry.evidence_relations.some((relation) => relation.evidence_id === "EV-LEDGER-CONTRADICT" && relation.relation === "contradict"));
});

test("44 recheck scope-invalid E3 cannot modify claim", async () => {
  const base = loadExample("simulation-only.example.json");
  const first = await runBound(base);
  const wrong = realEvidence({ evidence_id: "EV-WRONG-SCOPE", applies_to_segment: "unrelated segment", contradicts_claims: ["H1"], supporting_claims: [] });
  const result = await runBound(recheckInput(base, first.structured_output, [wrong]));
  const claim = result.structured_output.user_hypotheses.find((entry) => entry.hypothesis_id === "H1");
  assert.ok(!claim.contradicting_refs.includes("EV-WRONG-SCOPE"));
});

test("45 every Persona is covered by JTBD", async () => {
  const output = (await runBound(loadExample("input.example.json"))).structured_output;
  const covered = new Set(output.jobs_to_be_done.flatMap((job) => job.persona_ids));
  assert.ok(output.personas.every((persona) => covered.has(persona.persona_id)));
});

test("46 every Persona scenario includes do_nothing", async () => {
  const scenarios = (await runBound(loadExample("input.example.json"))).structured_output.scenarios_and_alternatives;
  assert.ok(scenarios.every((scenario) => scenario.alternatives.some((alternative) => alternative.alternative_type === "do_nothing")));
});

test("47 every Persona journey has all five stages", async () => {
  const required = ["awareness", "trial", "first_use", "continued_use", "referral"];
  const scenarios = (await runBound(loadExample("input.example.json"))).structured_output.scenarios_and_alternatives;
  for (const scenario of scenarios) assert.deepEqual(scenario.journey.map((stage) => stage.stage).sort(), [...required].sort());
});

test("48 functional attribution without product trace is downgraded", async () => {
  const input = loadExample("input.example.json");
  input.upstream_product_handoff = null;
  input.evidence_refs = [];
  const output = (await runBound(input)).structured_output;
  assert.ok(output.simulated_findings.task_test_matrix.filter((task) => task.result === "failed").every((task) => task.cause_type !== "functional" && task.cause_type !== "performance"));
});

test("49 top user problems deduplicate the same blocker", async () => {
  const problems = (await runBound(loadExample("input.example.json"))).structured_output.top_user_problems;
  assert.equal(problems.filter((problem) => /导出/u.test(problem.question)).length, 1);
});

test("50 rejected plan candidate does not mark valid S6 failed", async () => {
  const result = await runBound(loadExample("simulation-only.example.json"), { badUpgrade: true });
  assert.ok(result.structured_output.execution_log.some((entry) => entry.step_id === "s6" && entry.outcome === "candidate_rejected"));
  assert.ok(!result.structured_output.execution_log.some((entry) => entry.step_id === "s6" && entry.outcome === "failed"));
});

test("51 max applicable tier excludes out-of-scope E5", async () => {
  const input = loadExample("simulation-only.example.json");
  input.existing_user_evidence = [realEvidence({ kind: "payment_record", tier: "E5", applies_to_segment: "unrelated segment", supporting_claims: [], contradicts_claims: ["H2"], valid_for_dimensions: ["willingness_to_pay"] })];
  const summary = (await runBound(input)).structured_output.evidence_level_summary;
  assert.equal(summary.max_ingested_tier, "E5");
  assert.notEqual(summary.max_applicable_tier, "E5");
});

test("52 corrected fixture E3 supports H1 and contradicts H2", () => {
  const evidence = loadExample("input.example.json").existing_user_evidence.find((entry) => entry.evidence_id === "EV-USR-EXT-0003");
  assert.deepEqual(evidence.supporting_claims, ["H1"]);
  assert.deepEqual(evidence.contradicts_claims, ["H2"]);
});

test("53 trusted state hash recomputes exactly", async () => {
  const structured = (await runBound(loadExample("input.example.json"))).structured_output;
  assert.equal(computeStateHash(structured), structured.run_manifest.state_hash);
});
