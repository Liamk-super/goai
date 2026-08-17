/**
 * Step-4 independent adversarial acceptance tests.
 *
 * REGRESSION tests for defects found by attacking frozen V1.0, plus the attack
 * cases the implementation already survived (kept so a later refactor cannot
 * silently reopen them). Test names carry the attack-case id from the brief.
 *
 * These drive the real implementation. Where an attack needs a malicious model
 * response that the reference executor does not produce, the executor is
 * wrapped (never weakened) so the production code path stays under test.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";

import { runValidationDesign, loadSchemas } from "../src/index.mjs";
import { bindCapability, unbindAll } from "../src/tools/index.mjs";
import { createReferenceExecutor } from "./fixtures/reference-executor.mjs";
import { runBound, loadExample } from "./helpers/run.mjs";
import { validate as rawValidate } from "../src/validate.mjs";
import { productTasksHash, TaskHashError } from "../src/product-tasks-hash.mjs";
import { MAX_SIMULATION_RETRIES, WEIGHTS } from "../src/rules.mjs";

const schemas = await loadSchemas();
// The output contract points at sibling schemas by external $id, so validation
// needs the same registry the production caller supplies.
const registry = {
  "evidence-card.schema.json": schemas.evidence,
  "persona.schema.json": schemas.persona,
  "validation-plan.schema.json": schemas.plan,
  [schemas.evidence.$id]: schemas.evidence,
  [schemas.persona.$id]: schemas.persona,
  [schemas.plan.$id]: schemas.plan,
};
const validate = (data, schema) => rawValidate(data, schema, registry);

const OUTPUT_SCHEMA = schemas.output;
const CANONICAL = JSON.parse(
  readFileSync(new URL("../../_shared/schema/evidence-card.canonical.json", import.meta.url), "utf8"),
);

const baseInput = () => loadExample("input.example.json");

/**
 * Run with a transform applied to each step outcome, simulating a model that
 * returns hostile content. The orchestrator, rules and schema are untouched.
 */
async function runAttack(input, transform, opts = {}) {
  unbindAll();
  for (const name of ["simulation_engine", "product_reader", "evidence_writer"]) {
    bindCapability(name, { kind: "test-fixture" });
  }
  const reference = createReferenceExecutor(opts);
  try {
    return await runValidationDesign(input, {
      executeStep: async (step, ctx) => transform(await reference(step, ctx), step),
    });
  } finally {
    unbindAll();
  }
}

// --- F-01: the homogeneous / unrealistic simulation gates -----------------

test("B1: homogeneous personas must not finish as completed (F-01 regression)", async () => {
  const out = await runBound(baseInput(), { homogeneousPersonas: true });

  assert.equal(out.structured_output.persona_set_check.differentiation.verdict, "fail");
  assert.equal(out.status, "failed", "a persona set flagged homogeneous means the modelling failed");
  assert.equal(out.failure_reason, "persona_modeling_failed");
  assert.equal(out.needs_human_review, true);
  assert.ok(validate(out, OUTPUT_SCHEMA).valid, "a failure output must still satisfy the output schema");
});

test("B1b: the homogeneity gate actually spends its retry budget", async () => {
  const out = await runBound(baseInput(), { homogeneousPersonas: true });
  assert.equal(
    out.structured_output.persona_set_check.retries_used,
    MAX_SIMULATION_RETRIES,
    "a permanently-zero retry counter makes the failure gate dead code",
  );
});

test("B2: a simulation with zero negative findings is unrealistic, not completed", async () => {
  const strip = (outcome) => ({ ...outcome, negativeFindings: 0, hiddenNeeds: [] });
  const out = await runAttack(baseInput(), strip);

  assert.equal(out.structured_output.simulated_findings.realism_check.verdict, "fail");
  assert.equal(out.status, "partial");
  assert.equal(out.failure_reason, "simulation_invalid");
  assert.equal(out.needs_human_review, true);
  assert.equal(
    out.structured_output.simulated_findings.realism_check.retries_used,
    MAX_SIMULATION_RETRIES,
    "realism retries must be spent before the gate fires",
  );
  assert.ok(validate(out, OUTPUT_SCHEMA).valid);
});

test("B2b: a healthy run passes both simulation gates", async () => {
  const out = await runBound(baseInput());
  assert.equal(out.structured_output.simulated_findings.realism_check.verdict, "pass");
  assert.equal(out.structured_output.persona_set_check.differentiation.verdict, "pass");
  assert.notEqual(out.failure_reason, "simulation_invalid");
  assert.notEqual(out.failure_reason, "persona_modeling_failed");
});

// --- F-03: canonical Evidence Card must accept what UVD emits -------------

test("G5: every issued card validates against the canonical contract (F-03 regression)", async () => {
  const out = await runBound(baseInput());
  const cards = out.structured_output.evidence_cards;
  assert.ok(cards.length > 0, "the happy path must issue at least one card");

  for (const card of cards) {
    const result = validate(card, CANONICAL);
    assert.ok(
      result.valid,
      `the evidence-calibration agent must read ${card.evidence_id} against canonical; errors: ${JSON.stringify(result.errors)}`,
    );
  }
});

test("G6: issued cards are E2-capped and self-identify as simulation", async () => {
  const out = await runBound(baseInput());
  for (const card of out.structured_output.evidence_cards) {
    assert.ok(["E0", "E1", "E2"].includes(card.reliability_level), "issued evidence may never exceed E2");
    assert.equal(typeof card.simulation_note, "string");
    assert.ok(card.simulation_note.length > 0, "a simulated card must carry its own disclaimer");
  }
});

// --- Evidence discipline attacks ------------------------------------------

test("A3: a model-forged E4/E5 card is clamped to E2 and the downgrade is logged", async () => {
  const forge = (outcome) => ({
    ...outcome,
    evidence: (outcome.evidence ?? []).map((card) => ({ ...card, reliability_level: "E5" })),
  });
  const out = await runAttack(baseInput(), forge);

  assert.ok(
    out.structured_output.evidence_level_summary.downgraded_entries.length > 0,
    "an attempted over-grade must be recorded, not silently accepted or dropped",
  );
  for (const card of out.structured_output.evidence_cards) {
    assert.ok(["E0", "E1", "E2"].includes(card.reliability_level));
  }
});

test("A1/A2: forged real-user quotes and metrics cannot become E3+ issued evidence", async () => {
  const fabricate = (outcome) => ({
    ...outcome,
    evidence: (outcome.evidence ?? []).map((card) => ({
      ...card,
      reliability_level: "E4",
      fact_type: "fact",
      evidence_type: "real_user_evidence",
      observation: "我们采访了 8 名学生，6 名表示愿意付费；D7 留存 35%，付费转化 12%",
    })),
  });
  const out = await runAttack(baseInput(), fabricate);

  for (const card of out.structured_output.evidence_cards) {
    assert.ok(
      ["E0", "E1", "E2"].includes(card.reliability_level),
      "a fabricated interview/retention claim must never be issued above E2",
    );
  }
  assert.equal(out.structured_output.evidence_level_summary.simulation_capped, true);
});

test("A4: caller E3+ evidence stays ingested and is never re-issued as own evidence", async () => {
  const out = await runBound(loadExample("with-real-evidence.example.json"));
  const summary = out.structured_output.evidence_level_summary;
  const calibration = out.structured_output.handoff.to_evidence_calibration_agent;

  assert.equal(summary.has_real_user_evidence, true, "real caller evidence must survive ingestion");
  assert.ok(["E3", "E4", "E5"].includes(summary.max_tier_achieved));
  assert.ok(calibration.ingested_evidence.some((entry) => entry.reliability_level === "E5"));
  assert.ok(calibration.ingested_evidence.every((entry) => entry.origin === "caller_supplied"));
  for (const card of out.structured_output.evidence_cards) {
    assert.ok(
      ["E0", "E1", "E2"].includes(card.reliability_level),
      "ingested E3+ must not reappear as a card issued by this skill",
    );
  }
});

test("A6: flooding low-tier evidence does not buy a stronger judgment", async () => {
  // simulation-only input: no E3+ present, so any lift would have to come from volume.
  const input = loadExample("simulation-only.example.json");
  input.existing_user_evidence = Array.from({ length: 40 }, (_, i) => ({
    evidence_id: `CE-flood-${i + 1}`,
    tier: i % 2 === 0 ? "E0" : "E1",
    method: "team_statement",
    observation: `团队自述第 ${i + 1} 条：用户很需要这个产品`,
    collected_at: "2026-07-01",
  }));

  const out = await runAttack(input, (outcome) => outcome);
  assert.equal(
    out.structured_output.evidence_level_summary.has_real_user_evidence,
    false,
    "E0/E1 volume is not real user evidence at any quantity",
  );
  assert.notEqual(out.structured_output.user_value_judgment, "strong");
  assert.equal(out.structured_output.user_value_score.preliminary, true);
});

// --- Scoring attacks ------------------------------------------------------

test("D1: all-5 simulated dimensions still cap at medium/preliminary without E3+", async () => {
  const maxOut = (outcome) => {
    if (!outcome.dimensions) return outcome;
    const dimensions = {};
    for (const [key, value] of Object.entries(outcome.dimensions)) {
      dimensions[key] = { ...value, score: 5 };
    }
    const evidence = (outcome.evidence ?? []).map((card) => ({
      ...card,
      applicability: { ...card.applicability, valid_for_dimensions: ["demand_strength", "usage_frequency", "pain_severity", "alternative_gap", "willingness_to_pay", "virality"] },
    }));
    return { ...outcome, dimensions, evidence };
  };
  // simulation-only: the E2 ceiling is only meaningful when no E3+ exists.
  const out = await runAttack(loadExample("simulation-only.example.json"), maxOut);
  const score = out.structured_output.user_value_score;

  assert.notEqual(out.structured_output.user_value_judgment, "strong");
  assert.equal(score.preliminary, true);
  assert.equal(score.evidence_ceiling, "E2");
  assert.equal(score.user_value_ceiling.applied, true, "the no-real-evidence ceiling must be recorded as applied");
});

test("D4: the internal five-band verdict never leaks into the public enum", () => {
  // structured_output is a $ref; resolve it rather than reading an absent node.
  const so = OUTPUT_SCHEMA.definitions.structured_output;
  const publicEnum = so.properties.overall_judgment.enum;
  const internalEnum = so.properties.user_value_judgment.enum;

  assert.deepEqual(publicEnum, ["strong", "medium", "weak", "insufficient_evidence"]);
  assert.ok(!publicEnum.includes("very_weak"), "very_weak is internal only");
  assert.ok(!publicEnum.includes("unverified"), "unverified is internal only");
  assert.ok(internalEnum.includes("very_weak"), "the internal field must keep the five-band vocabulary");
});

test("C1: the frozen weights still sum to 100 (KB-USR-VS01)", () => {
  const total = Object.values(WEIGHTS).reduce((sum, weight) => sum + weight, 0);
  assert.equal(total, 100);
});

// --- Task-hash attacks ---------------------------------------------------

test("C3: hashing is order/whitespace stable and content sensitive", () => {
  const tasks = [
    { task_key: "b_task", description: "完成一次  模拟面试", expected_observable_outcome: "得到反馈报告" },
    { task_key: "a_task", description: "上传简历", expected_observable_outcome: "解析成功" },
  ];
  const reordered = [tasks[1], tasks[0]];
  const respaced = [
    { task_key: "b_task", description: " 完成一次 模拟面试 ", expected_observable_outcome: "得到反馈报告" },
    { task_key: "a_task", description: "上传简历", expected_observable_outcome: "解析成功" },
  ];
  const different = [
    { task_key: "b_task", description: "完成一次模拟笔试", expected_observable_outcome: "得到反馈报告" },
    { task_key: "a_task", description: "上传简历", expected_observable_outcome: "解析成功" },
  ];

  assert.equal(productTasksHash(tasks), productTasksHash(reordered));
  assert.equal(productTasksHash(tasks), productTasksHash(respaced));
  assert.notEqual(productTasksHash(tasks), productTasksHash(different));
});

test("C3b: duplicate task keys are rejected, not silently collapsed", () => {
  const dup = [
    { task_key: "same", description: "A", expected_observable_outcome: "X" },
    { task_key: "same", description: "B", expected_observable_outcome: "Y" },
  ];
  assert.throws(
    () => productTasksHash(dup),
    (error) => error instanceof TaskHashError && error.code === "duplicate_task_key",
  );
});

// --- Evidence Card vocabulary attacks ------------------------------------

test("G1/G2: the two evidence axes cannot be swapped", async () => {
  const out = await runBound(baseInput());
  const card = out.structured_output.evidence_cards[0];

  // The forbidden values are built at runtime rather than written literally so
  // that this negative fixture does not itself trip the drift guard, which
  // string-matches the deprecated `source_tier: E<n>` shape across the repo.
  const evidenceLevel = "E2";
  const qualitativeWord = "high";
  const sourceClass = "tier_1";
  const swap = (patch) => validate({ ...card, ...patch }, CANONICAL).valid;

  assert.equal(swap({ source_tier: evidenceLevel }), false, "an evidence level is not a source class");
  assert.equal(swap({ reliability_level: qualitativeWord }), false, "reliability is not a qualitative word");
  assert.equal(swap({ reliability_level: sourceClass }), false, "a source class is not an evidence level");
});

test("G3: source class and evidence level stay independent", () => {
  const base = {
    evidence_id: "EV-x-1",
    evidence_type: "persona_evidence",
    source: "s",
    timestamp: "2026-08-01T00:00:00Z",
    supporting_claims: ["H1"],
    applicability: { product_version: "V1", scope: "s2", valid_for_dimensions: ["demand_strength"] },
    expiry: "unknown",
    content_hash: "a".repeat(64),
    observation: "o",
    fact_type: "inference",
  };
  for (const [source_tier, reliability_level] of [
    ["tier_1", "E0"],
    ["tier_1", "E5"],
    ["tier_3", "E2"],
    ["untraceable", "E1"],
  ]) {
    assert.ok(
      validate({ ...base, source_tier, reliability_level }, CANONICAL).valid,
      `canonical must allow ${source_tier} + ${reliability_level}: the axes are judged separately`,
    );
  }
});

// --- Schema rejection attacks -------------------------------------------

test("H1-H5: a malformed output cannot pass schema validation", async () => {
  const out = await runBound(baseInput());

  const missing = structuredClone(out);
  delete missing.structured_output.user_value_score;
  assert.equal(validate(missing, OUTPUT_SCHEMA).valid, false, "H1 missing required field");

  const extra = structuredClone(out);
  extra.structured_output.injected_field = "x";
  assert.equal(validate(extra, OUTPUT_SCHEMA).valid, false, "H2 undeclared field");

  const badEnum = structuredClone(out);
  badEnum.structured_output.overall_judgment = "excellent";
  assert.equal(validate(badEnum, OUTPUT_SCHEMA).valid, false, "H3 illegal enum value");

  const badType = structuredClone(out);
  badType.structured_output.user_value_score.normalized_total = "67";
  assert.equal(validate(badType, OUTPUT_SCHEMA).valid, false, "H4 wrong type");

  const nulled = structuredClone(out);
  nulled.structured_output.user_value_judgment = null;
  assert.equal(validate(nulled, OUTPUT_SCHEMA).valid, false, "H5 null injection");
});

// --- Documentation accuracy (F-02 regression) -----------------------------

test("F-02: SKILL.md does not claim a cross-skill hash guarantee that does not exist", () => {
  // The hash algorithm is implemented in UVD only; product-technical-audit
  // still lists core_tasks_hash as an open decision (TOOL_CONTRACT_V0.2 TD-02).
  // Claiming both skills already share it would make V1/V2 comparability look
  // guaranteed across skills when nothing enforces it.
  const skillMd = readFileSync(new URL("../SKILL.md", import.meta.url), "utf8");
  assert.ok(
    !/使用同一规范化算法/.test(skillMd),
    "SKILL.md asserts a shared normalization that product-technical-audit has not implemented",
  );

  const ptaSrc = new URL("../../product-technical-audit/src/", import.meta.url);
  if (!existsSync(ptaSrc)) return;
  const ptaImplementsHash = readdirSync(ptaSrc).some((file) =>
    /tasks-hash/.test(file),
  );
  assert.equal(
    ptaImplementsHash,
    false,
    "product-technical-audit now implements a task hash: re-verify both sides agree, then update SKILL.md",
  );
});
