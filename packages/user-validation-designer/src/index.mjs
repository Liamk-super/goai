/**
 * user-validation-designer — S1..S6 orchestrator.
 *
 * V1.0.5 scope: the state machine, every deterministic gate, and the output
 * contract are real and enforced. The SIMULATION CONTENT (personas, interview
 * text, task outcomes) comes from a bound `simulation_engine` adapter injected
 * via deps.executeStep. With nothing bound, steps report `not_executable` and
 * the skill returns a blocked/partial envelope.
 *
 * It never fabricates a persona, a user quote, a task result, or an evidence
 * card. That is the single most important property of this file: an unbound run
 * produces an honest empty result, not a plausible fake one.
 */

import { readFile } from "node:fs/promises";

import { validate } from "./validate.mjs";
import { checkTargetUserBreadth, checkObjectiveScope, scanForExternalActionRequests, CLARIFICATION_QUESTIONS } from "./admission.mjs";
import { scanInput, redact } from "./pii-scan.mjs";
import { checkAvailability } from "./tools/index.mjs";
import { isRetrieverBound, kbIdsFor } from "./knowledge.mjs";
import { buildHandoff } from "./handoff.mjs";
import {
  renderHumanReport,
  renderHumanReportHtml,
  renderSummaryReport,
  renderSummaryReportHtml,
  renderFullReport,
  renderFullReportHtml,
  buildUserSpecialistReportV2,
  selectUserSpecialistReportV2,
} from "./presentation.mjs";
import {
  checkBaseline,
  checkHypothesisInheritance,
  checkHypothesisIdentity,
  checkThresholdIntegrity,
  buildLedger,
  progressVerdict,
  checkSettledReopen,
  comparePersonas,
  compareTasks,
} from "./regression.mjs";
import { vetPlans, linkHypothesesToPlans } from "./validation-plans.mjs";
import {
  SIMULATION_DISCLAIMER,
  normalizeIssuedCards,
  ingestExistingEvidence,
  mapClaimApplicability,
  maxTier,
  hasRealUserEvidence,
  tierDistribution,
  buildEvidenceEffectLedger,
  deriveClaimEvidenceState,
} from "./evidence.mjs";
import { computeStateHash, computeRegressionBaselineHash } from "./state-integrity.mjs";
import { productTasksHash } from "./product-tasks-hash.mjs";
import {
  buildPersonaOutcomes,
  crossReferenceDiagnostics,
  detectPromptInjection,
  exactPersonaRecords,
  exclusionsFromInput,
  taskMatrixDiagnostics,
  uniqueIdDiagnostics,
} from "./integrity.mjs";
import {
  emptyDimensions,
  emptyFlags,
  applyCounting,
  scoreTotals,
  uncountedCount,
  SIMULATION_CEILING,
  userValueJudgment,
  toOverallJudgment,
  evidenceConfidence,
  checkPersonaSet,
  personaEligibility,
  checkRealism,
  rankHypotheses,
  resolveConflict,
  resolveSwitchingForces,
  MAX_SIMULATION_RETRIES,
} from "./rules.mjs";

const schemaDir = new URL("../schema/", import.meta.url);

/**
 * KB-USR-VS03 forbids reading the total as "how much users like it" or as a
 * success probability. The note travels with the score so a downstream reader
 * cannot pick up the number without the caveat.
 */
const SCORING_NOTE =
  "本分数是需求侧证据强度的序数，不是用户喜欢程度，也不是成功率。未计分维度表示证据不足，不等于低分。";

export async function loadSchemas() {
  const read = async (name) => JSON.parse(await readFile(new URL(name, schemaDir), "utf8"));
  const [input, output, evidence, persona, plan] = await Promise.all([
    read("input.schema.json"),
    read("output.v0.2.schema.json"),
    read("evidence-card.schema.json"),
    read("persona.schema.json"),
    read("validation-plan.schema.json"),
  ]);
  return { input, output, evidence, persona, plan };
}

/**
 * External $ref registry. Sub-schemas are referenced by their canonical $id
 * (launchscope://...) inside output.v0.2.schema.json, and by filename elsewhere, so
 * both keys are registered for each schema.
 */
function buildRegistry(schemas) {
  const registry = {};
  const add = (filename, schema) => {
    registry[filename] = schema;
    if (typeof schema?.$id === "string") registry[schema.$id] = schema;
  };
  add("evidence-card.schema.json", schemas.evidence);
  add("persona.schema.json", schemas.persona);
  add("validation-plan.schema.json", schemas.plan);
  return registry;
}

/** S1..S6 plan, with the KB IDs that govern each step. */
export function buildPlan() {
  return {
    steps: [
      { id: "s1", name: "user_definition_and_admission", capabilities: [], kb: kbIdsFor("target_user_admission") },
      { id: "s2", name: "persona_and_jtbd", capabilities: ["simulation_engine"], kb: kbIdsFor("persona_modeling") },
      { id: "s3", name: "scenario_and_alternatives", capabilities: ["simulation_engine"], kb: kbIdsFor("scenario_and_alternatives") },
      { id: "s4a", name: "first_experience_simulation", capabilities: ["simulation_engine", "product_reader"], kb: kbIdsFor("first_experience") },
      { id: "s4b", name: "core_task_test", capabilities: ["simulation_engine", "product_reader"], kb: kbIdsFor("task_test") },
      { id: "s5", name: "hypotheses_and_problems", capabilities: ["simulation_engine"], kb: kbIdsFor("interview_simulation") },
      { id: "s6", name: "validation_plan_design", capabilities: [], kb: kbIdsFor("validation_design") },
    ],
  };
}

function emptyStructuredOutput() {
  return {
    simulation_disclaimer: SIMULATION_DISCLAIMER,
    overall_judgment: "insufficient_evidence",
    user_value_judgment: "unverified",
    evidence_confidence: "low",
    user_value_score: {
      raw_total: null,
      counted_weight: 0,
      normalized_total: null,
      uncounted_dimension_count: 6,
      dimensions: emptyDimensions(),
      preliminary: true,
      evidence_ceiling: "E2",
      user_value_ceiling: { applied: false, ceiling: null, reason: "not assessed" },
    },
    target_user_definition: {
      admitted: false,
      breadth_check: { verdict: "too_broad", matched_broad_patterns: [], reason: "not assessed" },
      converged_segments: [],
      excluded_segments: [],
      clarification_questions: [],
    },
    personas: [],
    persona_outcomes: [],
    persona_set_check: {
      count: 0,
      archetype_coverage: { high_need: false, skeptic: false, edge_case: false },
      differentiation: { pairwise_min_distinct_keys: null, homogeneous_pairs: [], verdict: "fail" },
      retries_used: 0,
    },
    jobs_to_be_done: [],
    scenarios_and_alternatives: [],
    simulated_findings: {
      executed: { first_experience: false, task_test: false },
      skip_reasons: [],
      evidence_tier: "E2",
      first_experience: [],
      value_communication_failure: false,
      task_test_matrix: [],
      experience_issues: [],
      simulated_interview: [],
      hidden_needs: [],
      insights: [],
      politeness_feedback_removed: [],
      realism_check: { negative_findings_count: 0, hidden_needs_count: 0, verdict: "not_applicable", retries_used: 0 },
    },
    user_hypotheses: [],
    top_user_problems: [],
    validation_plans: [],
    deferred_validations: [],
    evidence_level_summary: {
      max_tier_achieved: "E0",
      max_ingested_tier: "E0",
      max_applicable_tier: "E0",
      has_real_user_evidence: false,
      tier_distribution: { E0: 0, E1: 0, E2: 0, E3: 0, E4: 0, E5: 0 },
      ingested_tier_distribution: { E0: 0, E1: 0, E2: 0, E3: 0, E4: 0, E5: 0 },
      applicable_tier_distribution: { E0: 0, E1: 0, E2: 0, E3: 0, E4: 0, E5: 0 },
      simulation_capped: true,
      downgraded_entries: [],
      per_claim: [],
      judgment_ceiling: { applied: false, ceiling: null, reason: "not assessed" },
    },
    evidence_effect_ledger: [],
    missing_information: [],
    conflicts: [],
    critical_issue: null,
    flags: emptyFlags(),
    out_of_scope_redirects: [],
    evidence_cards: [],
    handoff: buildHandoff({}),
    regression_comparison: null,
    run_manifest: null,
    integrity_diagnostics: [],
    rejected_output: [],
    execution_log: [],
    human_report: null,
    human_report_html: null,
    summary_report: null,
    summary_report_html: null,
    full_report: null,
    full_report_html: null,
  };
}

/**
 * Build an execution_log entry. `step_id` must be one of the unit ids in the
 * output contract, so pre-step gate work is attributed to the step it guards
 * (s1 for envelope/admission) rather than an invented "gate" unit.
 */
function logEntry({ step_id, capability = null, tool_call_id = null, outcome, detail, evidence_ids = [], retries = 0, kb_ids = [] }) {
  return { step_id, capability, tool_call_id, outcome, detail, evidence_ids, retries, kb_ids };
}

function blockedResult(taskId, failureReason, summary, extra = {}) {
  const structured = { ...emptyStructuredOutput(), ...(extra.structured_output ?? {}) };
  if (extra.execution_log) structured.execution_log = extra.execution_log;
  return {
    task_id: taskId ?? "unknown",
    status: extra.status ?? "blocked",
    result_summary: summary,
    structured_output: structured,
    evidence_refs: extra.evidence_refs ?? [],
    confidence: 0,
    risks: extra.risks ?? [],
    needs_human_review: extra.needs_human_review ?? false,
    failure_reason: failureReason,
    retryable: extra.retryable ?? true,
  };
}

/**
 * Run the skill.
 *
 * @param {object} rawInput
 * @param {object} [deps] { schemas, executeStep }
 *   executeStep(step, input, context) -> {
 *     status, detail, personas?, jobs?, scenarios?, firstExperience?, taskTests?,
 *     interview?, hiddenNeeds?, insights?, hypotheses?, plans?, dimensions?,
 *     flags?, evidence?, negativeFindings?, politenessRemoved?
 *   } | null
 */
export async function runValidationDesign(rawInput, deps = {}) {
  const schemas = deps.schemas ?? (await loadSchemas());
  const registry = buildRegistry(schemas);
  const executionLog = [];
  const clockValue = typeof deps.now === "function" ? deps.now() : deps.now;
  const now = clockValue ?? new Date().toISOString();

  // --- Gate 0: PII / credentials (A-21) -----------------------------------
  const pii = scanInput(rawInput);
  if (!pii.clean) {
    return blockedResult(
      rawInput?.task_id,
      "pii_in_input",
      "Blocked: the envelope carries personal data or credentials. Locations are reported; values are never echoed. " +
        "Re-submit with personal data removed or replaced by an opaque reference.",
      {
        retryable: false,
        needs_human_review: true,
        risks: pii.findings.map((finding) => `${finding.path}: ${finding.label} — ${finding.reason}`),
      },
    );
  }

  // --- Gate 1: input contract --------------------------------------------
  const inputCheck = validate(rawInput, schemas.input, registry);
  if (!inputCheck.valid) {
    return blockedResult(rawInput?.task_id, "schema_validation_failed", "Blocked: input does not satisfy the contract.", {
      needs_human_review: true,
      risks: inputCheck.errors.map((error) => `${error.path}: ${error.message}`),
    });
  }
  const input = rawInput;

  const computedTaskHash = productTasksHash(input.product_tasks);
  if (input.runtime?.product_tasks_hash && input.runtime.product_tasks_hash !== computedTaskHash) {
    return blockedResult(input.task_id, "script_mismatch", "Blocked: runtime.product_tasks_hash does not match the program-computed task baseline.", {
      needs_human_review: true,
      risks: [`declared ${input.runtime.product_tasks_hash}; computed ${computedTaskHash ?? "none"}`],
    });
  }

  // --- Gate 2: external action requests (A-23) ---------------------------
  const external = scanForExternalActionRequests(input);
  if (!external.clean) {
    return blockedResult(
      input.task_id,
      "external_action_requires_approval",
      "Blocked: the request asks this skill to act on real users (contact, send, publish, collect, or spend). " +
        "This skill designs validation plans; a human executes them after approval.",
      {
        retryable: false,
        needs_human_review: true,
        risks: external.findings.map((finding) => `${finding.path}: requested ${finding.label}`),
        structured_output: {
          out_of_scope_redirects: external.findings.map((finding) => ({
            question: `${finding.path} requests ${finding.label}`,
            redirect_to: "human_execution_after_approval",
          })),
          flags: { ...emptyFlags(), external_action_pending_approval: true },
        },
      },
    );
  }

  const objectiveScope = checkObjectiveScope(input.validation_goal);
  if (objectiveScope.fully_out_of_scope) {
    return blockedResult(input.task_id, "invalid_task_envelope", "Not executable: the objective is outside user validation; no Persona or simulation work was run.", {
      retryable: false,
      structured_output: { out_of_scope_redirects: objectiveScope.redirects },
    });
  }

  const availability = checkAvailability(input.runtime?.allowed_tools, deps.capabilityContext);
  const mode = input.runtime?.mode ?? "first_validation";
  if (mode === "evidence_recheck" && !input.previous_structured_output) {
    return blockedResult(input.task_id, "invalid_task_envelope", "Blocked: evidence_recheck requires previous_structured_output and will not regenerate Personas or task simulations.", {
      risks: ["previous_structured_output is required for evidence_recheck"],
    });
  }
  if (mode === "evidence_recheck") {
    const previous = input.previous_structured_output;
    const previousSchema = { definitions: schemas.output.definitions, $ref: "#/definitions/structured_output" };
    const previousCheck = validate(previous, previousSchema, registry);
    const manifest = previous?.run_manifest;
    const compatibleVersion = ["1.0.3", "1.0.4", "1.0.5"].includes(manifest?.skill_version);
    const identityMatches = manifest?.project_id === input.project_id;
    const versionMatches = manifest?.product_version === input.product_version;
    const scoringMatches = manifest?.scoring_schema_version === (input.runtime?.scoring_schema_version ?? "0.1");
    if (!previousCheck.valid || !compatibleVersion || !identityMatches || !versionMatches || !scoringMatches) {
      return blockedResult(input.task_id, "invalid_previous_state", "Blocked: previous_structured_output is malformed, stale, or belongs to another project/version/standard.", {
        retryable: false,
        needs_human_review: true,
        risks: [
          ...previousCheck.errors.map((error) => `${error.path}: ${error.message}`),
          ...(!compatibleVersion ? ["previous skill_version is not compatible with V1.0.5"] : []),
          ...(!identityMatches ? ["previous project identity does not match"] : []),
          ...(!versionMatches ? ["previous product_version does not match"] : []),
          ...(!scoringMatches ? ["previous scoring_schema_version does not match"] : []),
        ],
      });
    }
    const trustedHash = input.previous_state_hash;
    const storedHash = manifest?.state_hash;
    const recomputedHash = computeStateHash(previous);
    if (!trustedHash || !storedHash || trustedHash !== storedHash || recomputedHash !== storedHash) {
      return blockedResult(input.task_id, "previous_state_integrity_mismatch", "Blocked: previous state does not match the trusted Harness/storage hash.", {
        retryable: false,
        needs_human_review: true,
        risks: ["previous_structured_output failed immutable state hash verification"],
      });
    }
  }

  executionLog.push(
    logEntry({
      step_id: "s1",
      outcome: "completed",
      detail: `Envelope valid. Mode ${mode}. Retriever bound: ${isRetrieverBound()}. Capabilities: ${JSON.stringify(availability)}.`,
    }),
  );

  // --- Gate 3: regression baseline (R-01) --------------------------------
  let regressionState = null;
  if (mode === "version_regression" || input.previous_validation_results) {
    const trustedPreviousHash = input.previous_validation_results_hash;
    const recomputedPreviousHash = computeRegressionBaselineHash(input.previous_validation_results);
    if (!trustedPreviousHash || trustedPreviousHash !== recomputedPreviousHash) {
      return blockedResult(input.task_id, "previous_state_integrity_mismatch", "Blocked: previous validation baseline does not match the trusted Harness/storage hash.", {
        execution_log: executionLog,
        retryable: false,
        needs_human_review: true,
        risks: ["previous_validation_results failed immutable baseline verification"],
      });
    }
    if (input.previous_validation_results?.project_id !== input.project_id) {
      return blockedResult(input.task_id, "previous_state_integrity_mismatch", "Blocked: previous validation baseline belongs to another project.", {
        execution_log: executionLog,
        retryable: false,
        needs_human_review: true,
        risks: ["previous_validation_results project identity does not match"],
      });
    }
    const baseline = checkBaseline(input);
    if (!baseline.ok) {
      return blockedResult(
        input.task_id,
        baseline.failure_reason,
        `Blocked: ${baseline.reason}. Two rounds cannot be compared on different task baselines, ` +
          "and reporting them as comparable would overstate progress.",
        { execution_log: executionLog, risks: [baseline.reason] },
      );
    }
    regressionState = { baseline: baseline.baseline, previous: input.previous_validation_results };
    executionLog.push(
      logEntry({
        step_id: "s1",
        outcome: "completed",
        detail: `Regression baseline: task hash matches previous round (${baseline.baseline.current.slice(0, 12)}...).`,
      }),
    );
  }

  // --- S1: admission (A-03) ----------------------------------------------
  const breadth = checkTargetUserBreadth(input.target_users);
  if (breadth.verdict === "too_broad") {
    return blockedResult(
      input.task_id,
      "target_user_too_broad",
      `Blocked: ${breadth.reason} Persona modelling is refused; answer the three questions and re-run.`,
      {
        execution_log: [
          ...executionLog,
          logEntry({
            step_id: "s1",
            outcome: "blocked",
            detail: breadth.reason,
            kb_ids: kbIdsFor("target_user_admission"),
          }),
        ],
        structured_output: {
          flags: { ...emptyFlags(), target_user_too_broad: true },
          target_user_definition: {
            admitted: false,
            breadth_check: {
              verdict: breadth.verdict,
              matched_broad_patterns: breadth.matched_broad_patterns,
              reason: breadth.reason,
            },
            converged_segments: [],
            excluded_segments: exclusionsFromInput(input.target_users?.exclusions),
            clarification_questions: breadth.clarification_questions.length > 0 ? breadth.clarification_questions : [...CLARIFICATION_QUESTIONS],
          },
          missing_information: [
            {
              field: "target_users.segments",
              state: "missing",
              why_it_matters:
                "Without an executable user definition, every persona, score and validation plan would be built on a description that predicts no behaviour.",
              affected_units: ["s1", "s2", "s3", "s4a", "s4b", "s5", "s6"],
              affected_dimensions: [],
              how_to_obtain: "Answer 谁最痛 / 谁付钱 / 谁最先用 with a concrete, recruitable segment.",
            },
          ],
        },
      },
    );
  }

  const flags = emptyFlags();
  if (breadth.verdict === "borderline") flags.target_user_too_broad = false;
  flags.compliance_concern = hasMaterialComplianceConcern(input.constraints?.compliance_notes);

  executionLog.push(
    logEntry({
      step_id: "s1",
      outcome: "completed",
      detail: `Admission ${breadth.verdict}. ${breadth.reason}`,
      kb_ids: kbIdsFor("target_user_admission"),
    }),
  );

  // --- Evidence ingest ---------------------------------------------------
  // Caller-supplied evidence keeps its real tier but is NOT re-issued as this
  // skill's own evidence card: we did not observe it. It is referenced by id
  // and counted for scoring. `evidenceCards` holds only what this skill issues.
  const ingested = ingestExistingEvidence(input.existing_user_evidence, { collected_at: now, product_version: input.product_version });
  let ingestedRecords = ingested.records;
  const immutableEvidenceDiagnostics = [];
  const evidenceCards = [];
  if (mode === "evidence_recheck") {
    const previous = input.previous_structured_output;
    const previousIssued = structuredClone(previous.evidence_cards ?? []);
    for (const card of previousIssued) {
      const cardCheck = validate(card, schemas.evidence, registry);
      if (cardCheck.valid) evidenceCards.push(card);
    }
    const previousIngested = structuredClone(previous.handoff?.to_evidence_calibration_agent?.ingested_evidence ?? []);
    const previousById = new Map(previousIngested.map((record) => [record.evidence_id, record]));
    const acceptedNew = [];
    for (const record of ingestedRecords) {
      const prior = previousById.get(record.evidence_id);
      if (!prior) acceptedNew.push(record);
      else if (prior.content_hash !== record.content_hash) {
        immutableEvidenceDiagnostics.push({ code: "evidence_id_content_mismatch", ref: record.evidence_id, detail: "existing evidence_id was reused with changed content; old evidence retained and new record rejected" });
      }
    }
    const newIds = new Set(acceptedNew.map((record) => record.evidence_id));
    ingestedRecords = [...previousIngested.filter((record) => !newIds.has(record.evidence_id)), ...acceptedNew];
  }
  /** Scoring reads both sets; the output contract only carries evidenceCards. */
  const allEvidence = () => [...ingestedRecords, ...evidenceCards];
  const missingInformation = buildMissingInformation(input, availability, breadth);
  const downgraded = [...ingested.downgraded];
  const integrityDiagnostics = [...ingested.diagnostics, ...immutableEvidenceDiagnostics];
  if (mode === "evidence_recheck") {
    downgraded.unshift(...structuredClone(input.previous_structured_output?.evidence_level_summary?.downgraded_entries ?? []));
    integrityDiagnostics.unshift(...structuredClone(input.previous_structured_output?.integrity_diagnostics ?? []));
  }
  const skipReasons = [];

  // --- S2..S6 execution --------------------------------------------------
  const plan = buildPlan();
  const collected = {
    personas: [],
    jobs: [],
    scenarios: [],
    firstExperience: [],
    taskTests: [],
    interview: [],
    hiddenNeeds: [],
    insights: [],
    politenessRemoved: [],
    hypotheses: [],
    plans: [],
    negativeFindings: 0,
    conflictCandidates: [],
    experienceIssues: [],
  };
  if (mode === "evidence_recheck") {
    const previous = input.previous_structured_output;
    Object.assign(collected, {
      personas: structuredClone(previous.personas ?? []),
      jobs: structuredClone(previous.jobs_to_be_done ?? []),
      scenarios: structuredClone(previous.scenarios_and_alternatives ?? []),
      firstExperience: structuredClone(previous.simulated_findings?.first_experience ?? []),
      taskTests: structuredClone(previous.simulated_findings?.task_test_matrix ?? []),
      interview: structuredClone(previous.simulated_findings?.simulated_interview ?? []),
      hiddenNeeds: structuredClone(previous.simulated_findings?.hidden_needs ?? []),
      insights: structuredClone(previous.simulated_findings?.insights ?? []),
      hypotheses: structuredClone(previous.user_hypotheses ?? []),
      plans: structuredClone(previous.validation_plans ?? []),
      experienceIssues: structuredClone(previous.simulated_findings?.experience_issues ?? []),
      segments: structuredClone(previous.target_user_definition?.converged_segments ?? []),
      politenessRemoved: structuredClone(previous.simulated_findings?.politeness_feedback_removed ?? []),
    });
    Object.assign(flags, structuredClone(previous.flags ?? {}));
  }
  let dimensions = emptyDimensions();
  if (mode === "evidence_recheck") dimensions = structuredClone(input.previous_structured_output?.user_value_score?.dimensions ?? dimensions);
  let executedAny = mode === "evidence_recheck";
  // Retries are tracked per gate: a homogeneous persona set and an unrealistic
  // simulation are independent failures and must not consume each other's budget.
  let personaRetries = 0;
  let realismRetries = 0;
  let realismFailed = false;
  let taskStepExecuted = false;
  let workerFailureReason = null;
  let personaCheck = checkPersonaSet([]);
  const effectiveRetries = Math.min(MAX_SIMULATION_RETRIES, input.runtime?.max_simulation_retries ?? MAX_SIMULATION_RETRIES);
  const stepStates = new Map([["s1", "completed"]]);

  /**
   * Snapshot / restore of accumulated step state.
   *
   * A gate retry re-executes the step, so its first (rejected) attempt must not
   * leave personas, evidence or negative-finding counts behind: without this the
   * retry would score the union of a bad attempt and its replacement.
   */
  const snapshot = () => ({
    collected: structuredClone(collected),
    dimensions: structuredClone(dimensions),
    evidenceCount: evidenceCards.length,
    downgradedCount: downgraded.length,
    logCount: executionLog.length,
    missingCount: missingInformation.length,
  });
  const restore = (snap) => {
    for (const key of Object.keys(collected)) delete collected[key];
    Object.assign(collected, structuredClone(snap.collected));
    dimensions = structuredClone(snap.dimensions);
    evidenceCards.length = snap.evidenceCount;
    downgraded.length = snap.downgradedCount;
    executionLog.length = snap.logCount;
    missingInformation.length = snap.missingCount;
  };

  const executionSteps = mode === "evidence_recheck" ? [] : plan.steps.slice(1);
  for (const step of executionSteps) {
    // Step-level input gating (A-04 / A-05): a missing precondition means the
    // step is not executable. It never means "guess the outcome".
    const gate = gateStep(step, input, availability, stepStates);
    if (!gate.executable) {
      stepStates.set(step.id, "not_executable");
      skipReasons.push({ unit: step.id, reason: gate.reason, missing_input: gate.missing });
      executionLog.push(
        logEntry({
          step_id: step.id,
          capability: step.capabilities.join(",") || null,
          outcome: "not_executable",
          detail: `${gate.reason} No evidence produced; no result inferred.`,
          kb_ids: step.kb,
        }),
      );
      continue;
    }

    // Gate steps may be re-run: a homogeneous persona set (A-07/A-08) or a
    // complaint-free simulation (A-10) is a failed attempt, and KB-USR allows
    // up to MAX_SIMULATION_RETRIES retries before the round is declared failed.
    const attemptBudget = step.id === "s2" || step.id === "s5" ? effectiveRetries + 1 : 1;
    const before = snapshot();
    let gateFailed = false;

    for (let attempt = 0; attempt < attemptBudget; attempt += 1) {
      if (attempt > 0) restore(before);

      const runContext = { collected, evidenceCards, attempt, availability, capabilityContext: deps.capabilityContext ?? null };
      const outcome = await (deps.dispatchStep?.(step, input, runContext) ?? deps.executeStep?.(step, input, runContext) ??
        Promise.resolve(null));
      if (!outcome) {
        stepStates.set(step.id, "not_executable");
        executionLog.push(
          logEntry({
            step_id: step.id,
            capability: step.capabilities.join(",") || null,
            outcome: "not_executable",
            detail: "Capability reported available but no executor is bound; nothing was simulated.",
            kb_ids: step.kb,
          }),
        );
        gateFailed = false;
        break;
      }

      const workerStatus = ["completed", "partial", "failed", "blocked", "not_executable"].includes(outcome.status)
        ? outcome.status
        : "failed";
      if (["failed", "blocked", "not_executable"].includes(workerStatus)) {
        restore(before);
        stepStates.set(step.id, workerStatus);
        workerFailureReason = outcome.failure_reason === "tool_timeout" ? "tool_timeout" : "step_execution_failed";
        skipReasons.push({ unit: step.id, reason: outcome.detail ?? `Worker returned ${workerStatus}`, missing_input: "dependency_failed" });
        executionLog.push(logEntry({
          step_id: step.id,
          capability: step.capabilities.join(",") || null,
          tool_call_id: outcome.tool_call_id ?? `${input.task_id}:${step.id}:${attempt + 1}`,
          outcome: workerStatus,
          detail: `${redact(String(outcome.detail ?? "Worker failed")).slice(0, 300)}; output quarantined and not merged.`,
          kb_ids: step.kb,
        }));
        gateFailed = false;
        break;
      }

      executedAny = true;
      if (step.id === "s4b") taskStepExecuted = true;
      mergeOutcome(collected, outcome);

      // A-01: clamp every card this skill issues to E2.
      const clamped = normalizeIssuedCards(outcome.evidence ?? [], {
        unit: step.id,
        productVersion: input.product_version,
        timestamp: now,
        personaIds: collected.personas.map((persona) => persona.persona_id),
      });
      downgraded.push(...clamped.downgraded);
      for (const card of clamped.cards) {
        const cardCheck = validate(card, schemas.evidence, registry);
        if (!cardCheck.valid) {
          executionLog.push(
            logEntry({
              step_id: step.id,
              capability: "evidence_writer",
              outcome: "failed",
              detail: `Evidence card rejected: ${cardCheck.errors.map((e) => `${e.path} ${e.message}`).join("; ")}`,
            }),
          );
          continue;
        }
        evidenceCards.push(card);
      }

      for (const [key, value] of Object.entries(outcome.dimensions ?? {})) {
        if (dimensions[key]) dimensions[key] = { ...dimensions[key], ...value };
      }
      if (detectPromptInjection(outcome)) {
        flags.prompt_injection_observed = true;
        integrityDiagnostics.push({ code: "prompt_injection_observed", ref: step.id, detail: "untrusted product content contained instruction-like text; recorded but not executed" });
      }

      // S2 gate (A-07/A-08): a homogeneous persona set is a modelling failure.
      if (step.id === "s2") {
        personaCheck = checkPersonaSet(collected.personas);
        const scopeValidatedForPersona = validateEvidenceScopes(ingestedRecords, {
          personas: collected.personas,
          segments: [...(input.target_users?.segments ?? []), ...(collected.segments ?? []).flatMap((segment) => [segment.segment_id, segment.label])],
          diagnostics: [],
        });
        collected.personas = collected.personas.map((persona) => derivePersonaConfidence(persona, scopeValidatedForPersona));
        for (const persona of collected.personas) {
          const eligibility = personaEligibility(persona);
          persona.eligible_for_scoring = eligibility.eligible;
          if (!eligibility.eligible) {
            const lowConfidence = eligibility.reason === "low_confidence";
            missingInformation.push({
              field: `personas.${persona.persona_id}`,
              state: lowConfidence ? "low_confidence" : "missing",
              why_it_matters: lowConfidence
                ? "Persona has all required fields but confidence is low and no explicitly scoped E3+ record calibrated it; its simulated evidence cannot support scoring."
                : `Persona is missing ${eligibility.missing.join(", ")}; per KB-USR-G01 it cannot support scoring.`,
              affected_units: ["s2", "s5"],
              affected_dimensions: [],
              how_to_obtain: lowConfidence
                ? "Provide E3+ real-user evidence explicitly scoped to this Persona, or remodel with stronger traceable provenance."
                : "Complete the six required persona elements including explicit value and rejection thresholds.",
            });
          }
        }
        const jobCoverage = jobCoverageDiagnostics(collected.personas, collected.jobs);
        if (!jobCoverage.valid) integrityDiagnostics.push({ code: "jtbd_persona_coverage", ref: "s2", detail: jobCoverage.reason });
        gateFailed = personaCheck.differentiation.verdict === "fail" || !jobCoverage.valid;
        personaCheck = { ...personaCheck, retries_used: attempt };
        personaRetries = attempt;
      }

      if (step.id === "s3") {
        const completeness = exactPersonaRecords(collected.scenarios, collected.personas, { label: "scenario" });
        const researchIntegrity = scenarioResearchIntegrity(collected.scenarios, collected.personas);
        gateFailed = !completeness.valid || !researchIntegrity.valid;
        if (gateFailed) integrityDiagnostics.push({ code: "scenario_completeness", ref: "s3", detail: completeness.reason });
        for (const reason of researchIntegrity.reasons) integrityDiagnostics.push({ code: "scenario_research_integrity", ref: "s3", detail: reason });
      }

      if (step.id === "s4a") {
        const completeness = exactPersonaRecords(collected.firstExperience, collected.personas, { label: "first_experience" });
        gateFailed = !completeness.valid;
        if (gateFailed) integrityDiagnostics.push({ code: "first_experience_completeness", ref: "s4a", detail: completeness.reason });
      }

      if (step.id === "s4b") {
        const unexpected = taskMatrixDiagnostics(collected.personas, input.product_tasks ?? [], collected.taskTests);
        integrityDiagnostics.push(...unexpected.map((entry) => ({ code: "unexpected_task_record", ref: "s4b", detail: JSON.stringify(entry) })));
        const matrix = normalizeTaskMatrix({ personas: collected.personas, tasks: input.product_tasks ?? [], records: collected.taskTests });
        collected.taskTests = matrix.records;
        gateFailed = matrix.incomplete || unexpected.length > 0;
        if (gateFailed) integrityDiagnostics.push({ code: "task_matrix_integrity", ref: "s4b", detail: matrix.reason });
      }

      // S5 gate (A-10): zero complaints means the simulation is broken.
      if (step.id === "s5") {
        const realism = checkRealism({
          negativeFindings: collected.negativeFindings,
          hiddenNeeds: collected.hiddenNeeds.length,
          retriesUsed: attempt,
          executed: true,
          interviews: collected.interview,
          personaIds: collected.personas.map((persona) => persona.persona_id),
        });
        gateFailed = realism.verdict === "fail";
        realismRetries = attempt;
      }

      executionLog.push(
        logEntry({
          step_id: step.id,
          capability: step.capabilities.join(",") || null,
          tool_call_id: outcome.tool_call_id ?? `${input.task_id}:${step.id}:${attempt + 1}`,
          outcome: gateFailed ? (attempt + 1 < attemptBudget ? "skipped" : "failed") : workerStatus,
          detail: gateFailed
            ? `${outcome.detail ?? "executed"} | gate rejected this attempt (attempt ${attempt + 1}/${attemptBudget})`
            : (outcome.detail ?? "executed"),
          evidence_ids: clamped.cards.map((card) => card.evidence_id),
          retries: attempt,
          kb_ids: step.kb,
        }),
      );

      if (!gateFailed) break;
    }

    // The budget is spent and the gate still rejects: record the failure so the
    // status resolver can refuse to call this round completed.
    if (gateFailed) {
      const rejectedAttemptLogs = executionLog.slice(before.logCount);
      const normalizedRejectedTaskMatrix = step.id === "s4b" ? structuredClone(collected.taskTests) : null;
      restore(before);
      if (normalizedRejectedTaskMatrix) collected.taskTests = normalizedRejectedTaskMatrix;
      executionLog.push(...rejectedAttemptLogs);
      stepStates.set(step.id, "failed");
      if (step.id === "s2") flags.persona_homogeneous = true;
      if (step.id === "s5") {
        flags.simulation_unrealistic = true;
        realismFailed = true;
      }
    } else if (!stepStates.has(step.id)) {
      const last = [...executionLog].reverse().find((entry) => entry.step_id === step.id);
      stepStates.set(step.id, last?.outcome === "partial" ? "partial" : "completed");
    }
  }

  collected.personas = normalizePersonaPainPriority(collected.personas);
  normalizeSimulationFactTypes(collected, ingestedRecords);
  applyTechnicalAttributionGate(collected, input);
  enforceExperienceIssueExecutionGate(collected, {
    stepStates,
    ingestedRecords,
    evidenceCards,
    input,
    diagnostics: integrityDiagnostics,
  });
  const eligiblePersonaIds = collected.personas
    .filter((persona) => persona.eligible_for_scoring === true)
    .map((persona) => persona.persona_id);
  const normalizedMatrix = taskStepExecuted && stepStates.get("s4b") === "completed"
    ? normalizeTaskMatrix({
        personas: collected.personas,
        tasks: input.product_tasks ?? [],
        records: collected.taskTests,
      })
    : { records: [], incomplete: false, missing_pairs: [], reason: "Task step not executed." };
  if (taskStepExecuted && stepStates.get("s4b") === "completed") collected.taskTests = normalizedMatrix.records;
  if (taskStepExecuted && stepStates.get("s4b") === "completed" && normalizedMatrix.incomplete) {
    skipReasons.push({
      unit: "s4b",
      reason: normalizedMatrix.reason,
      missing_input: normalizedMatrix.missing_pairs.join(",") || "eligible_persona",
    });
    executionLog.push(
      logEntry({
        step_id: "s4b",
        outcome: "candidate_rejected",
        detail: normalizedMatrix.reason,
        kb_ids: kbIdsFor("task_test"),
      }),
    );
  }

  collected.scenarios = normalizeSwitchingScenarios(collected.scenarios, collected.personas);
  if (mode === "evidence_recheck") personaCheck = checkPersonaSet(collected.personas);
  flags.high_switching_friction = collected.scenarios.some(
    (scenario) => scenario.flags?.high_switching_friction === true,
  );
  flags.pseudo_demand_risk = collected.scenarios.some((scenario) => scenario.flags?.pseudo_demand_risk === true);
  flags.value_communication_failure = computeValueCommunicationFailure(collected.firstExperience);
  flags.retention_risk = collected.scenarios.some((scenario) => (scenario.journey ?? []).some((stage) => stage.stage === "continued_use" && stage.drop_off_risk === "high"));
  flags.politeness_only_feedback = collected.politenessRemoved.length > 0;

  integrityDiagnostics.push(...uniqueIdDiagnostics({
    persona: { records: collected.personas, field: "persona_id" },
    scenario: { records: collected.scenarios, field: "scenario_id" },
    job: { records: collected.jobs, field: "job_id" },
    hypothesis: { records: collected.hypotheses, field: "hypothesis_id" },
    plan: { records: collected.plans, field: "plan_id" },
    issue: { records: collected.experienceIssues, field: "issue_id" },
    evidence: { records: [...ingestedRecords, ...evidenceCards], field: "evidence_id" },
  }));
  integrityDiagnostics.push(...crossReferenceDiagnostics({
    personas: collected.personas,
    jobs: collected.jobs,
    hypotheses: collected.hypotheses,
    evidence: [...ingestedRecords, ...evidenceCards],
    issues: collected.experienceIssues,
    dimensions,
    root: collected,
  }));

  // --- Hypotheses: rank, then link to plans (A-12 / A-13) ----------------
  let rankedHypotheses = rankHypotheses(collected.hypotheses);
  const mappedEvidence = mapClaimApplicability(ingestedRecords, rankedHypotheses);
  rankedHypotheses = mappedEvidence.hypotheses;
  ingestedRecords = validateEvidenceScopes(mappedEvidence.records, {
    personas: collected.personas,
    segments: [...(input.target_users?.segments ?? []), ...(collected.segments ?? []).flatMap((segment) => [segment.segment_id, segment.label])],
    diagnostics: integrityDiagnostics,
  });
  integrityDiagnostics.push(...mappedEvidence.diagnostics);
  const evidenceEffectLedger = buildEvidenceEffectLedger(rankedHypotheses, [...evidenceCards, ...ingestedRecords]);
  rankedHypotheses = rankHypotheses(deriveClaimEvidenceState(rankedHypotheses, evidenceEffectLedger, [...evidenceCards, ...ingestedRecords]));
  dimensions = applyEvidenceEffectsToDimensions(dimensions, rankedHypotheses, evidenceEffectLedger, [...evidenceCards, ...ingestedRecords]);
  collected.conflictCandidates.push(...buildEvidenceRelationConflicts(rankedHypotheses, ingestedRecords, evidenceCards));
  collected.conflictCandidates.push(...buildRealEvidenceConflicts(rankedHypotheses, ingestedRecords));

  const perClaim = rankedHypotheses.map((hypothesis) => ({
    claim_id: hypothesis.hypothesis_id,
    claim: hypothesis.statement,
    current_tier: hypothesis.current_evidence_level ?? "E0",
    target_tier: hypothesis.target_evidence_level ?? "E3",
    upgrade_plan_id: null,
    upgradable: !["validated", "falsified", "abandoned"].includes(hypothesis.status),
  }));
  const claimTiers = Object.fromEntries(perClaim.map((claim) => [claim.claim_id, claim.current_tier]));

  // --- S6: vet plans (A-14 / A-15 / A-16) -------------------------------
  const vetted = vetPlans(collected.plans, {
    claimTiers,
    constraints: input.constraints,
    hypotheses: rankedHypotheses,
    personaIds: new Set(collected.personas.map((persona) => persona.persona_id)),
    excludedSegments: input.target_users?.exclusions ?? [],
  });
  if (vetted.rejected.length > 0) {
    const s6Completion = [...executionLog].reverse().find((entry) => entry.step_id === "s6" && entry.outcome === "completed");
    if (s6Completion) s6Completion.outcome = "completed_with_rejections";
  }
  for (const rejection of vetted.rejected) {
    executionLog.push(
      logEntry({
        step_id: "s6",
        outcome: "candidate_rejected",
        detail: `Plan ${rejection.plan_id} rejected: ${rejection.problems.join("; ")}`,
        kb_ids: kbIdsFor("validation_plan_design"),
      }),
    );
  }
  if (vetted.plans.some((planItem) => planItem.needs_human_review)) {
    flags.external_action_pending_approval = true;
  }

  const deferredByConstraint = new Map((vetted.deferred ?? []).map((entry) => [entry.hypothesis_id, entry]));
  rankedHypotheses = rankedHypotheses.map((hypothesis) => deferredByConstraint.has(hypothesis.hypothesis_id)
    ? { ...hypothesis, deferred_reason: `constraint_gap: ${deferredByConstraint.get(hypothesis.hypothesis_id).reason}` }
    : hypothesis);
  const linked = linkHypothesesToPlans(rankedHypotheses, vetted.plans);
  for (const claim of perClaim) {
    const owner = vetted.plans.find((planItem) => planItem.hypothesis_id === claim.claim_id);
    claim.upgrade_plan_id = owner ? owner.plan_id : null;
    if (owner) {
      const upgrade = owner.evidence_upgrade?.find((item) => item.claim_id === claim.claim_id);
      claim.target_tier = upgrade?.to_tier ?? owner.target_evidence_level ?? claim.target_tier;
    } else if (!claim.upgradable) {
      claim.target_tier = claim.current_tier;
    }
  }

  // --- Conflicts (A-06) --------------------------------------------------
  const conflicts = buildConflicts(collected, allEvidence(), {
    diagnostics: integrityDiagnostics,
    productVersion: input.product_version,
  });
  if (conflicts.length > 0) flags.conflict = true;
  const unresolvedRealConflict = conflicts.some((conflict) => conflict.resolution === "unresolved" && conflict.side_a?.tier >= "E3" && conflict.side_b?.tier >= "E3");

  // --- Scoring (A-17 .. A-20) -------------------------------------------
  // Scoring reads ingested real evidence too: a dimension backed by an E3
  // interview must count, even though this skill issued no card for it.
  dimensions = attachApplicableRealEvidence(dimensions, ingestedRecords);
  dimensions = applyCounting(dimensions, allEvidence(), { eligiblePersonaIds });
  const totals = scoreTotals(dimensions);
  const judgmentEvidenceIds = new Set(
    Object.values(dimensions).flatMap((dimension) => dimension.evidence_refs ?? []),
  );
  const judgmentEvidence = allEvidence().filter((entry) => judgmentEvidenceIds.has(entry.evidence_id));
  const realEvidence = hasRealUserEvidence(judgmentEvidence);
  if ((input.existing_user_evidence ?? []).some((entry) => ["E3", "E4", "E5"].includes(entry.tier)) && !realEvidence) {
    missingInformation.push({
      field: "existing_user_evidence.applicability",
      state: "insufficient_real_evidence",
      why_it_matters: "E3+ evidence exists in the package but none is explicitly applicable to a scored dimension, so it cannot unlock the judgment ceiling.",
      affected_units: ["s5", "s7_synthesis"],
      affected_dimensions: [],
      how_to_obtain: "Declare supporting_claims or valid_for_dimensions for the real evidence and verify that the scoped claim is used by the judgment.",
    });
  }
  const valueJudgment = userValueJudgment({
    normalized_total: totals.normalized_total,
    dimensions,
    hasRealUserEvidence: realEvidence,
  });

  // --- Status resolution -------------------------------------------------
  let status;
  let failureReason = null;
  let retryable = true;
  let needsHumanReview = false;

  if (!executedAny) {
    status = "blocked";
    failureReason = "tool_unavailable";
  } else if (flags.persona_homogeneous && personaRetries >= effectiveRetries) {
    status = "failed";
    failureReason = "persona_modeling_failed";
    needsHumanReview = true;
  } else if (realismFailed && realismRetries >= effectiveRetries) {
    status = "partial";
    failureReason = "simulation_invalid";
    needsHumanReview = true;
  } else if (stepStates.get("s4b") === "failed") {
    status = "partial";
    failureReason = "incomplete_task_matrix";
    needsHumanReview = true;
  } else if ([...stepStates.values()].some((state) => ["failed", "blocked"].includes(state))) {
    status = "failed";
    failureReason = workerFailureReason ?? "step_execution_failed";
    needsHumanReview = true;
  } else if (integrityDiagnostics.some((entry) => ["duplicate_id", "unknown_reference", "evidence_id_content_mismatch"].includes(entry.code))) {
    status = "failed";
    failureReason = "invalid_output_schema";
    needsHumanReview = true;
  } else if (unresolvedRealConflict) {
    status = "partial";
    failureReason = "conflicting_real_evidence";
    needsHumanReview = true;
  } else if (linked.orphans.length > 0) {
    // A-12: an open assumption with neither a plan nor an explicit deferral is
    // exactly the failure this skill exists to prevent, so it is a hard failure
    // rather than a quiet omission.
    status = "failed";
    failureReason = "unsupported_validation_method";
    needsHumanReview = true;
  } else if (skipReasons.length > 0) {
    status = "partial";
    // Only reasons in the frozen enum may be emitted. A skipped task test is
    // missing_product_task; anything else partial carries no failure_reason,
    // because "partial" is already the signal.
    failureReason = !Array.isArray(input.product_tasks) || input.product_tasks.length === 0
      ? "missing_product_task"
      : skipReasons.some((skip) => skip.missing_input === "product_profile.url|product_profile.experience_report_ref")
        ? "insufficient_product_context"
      : skipReasons.some((skip) => skip.unit === "s4b")
        ? "incomplete_task_matrix"
        : null;
  } else if ([...stepStates.values()].some((state) => state === "partial")) {
    status = "partial";
    failureReason = null;
  } else {
    status = "completed";
    retryable = false;
  }

  if (flags.compliance_concern || flags.external_action_pending_approval) needsHumanReview = true;

  const realJudgmentEvidence = judgmentEvidence.filter((record) => ["E3", "E4", "E5"].includes(record.reliability_level));
  const allRealEvidenceUnderpowered = realJudgmentEvidence.length > 0 && realJudgmentEvidence.every(
    (record) => ["underpowered", "unknown"].includes(record.sample_adequacy),
  );
  const confidenceLabel = evidenceConfidence({ dimensions, hasRealUserEvidence: realEvidence, flags, status, allRealEvidenceUnderpowered, targetBorderline: breadth.verdict === "borderline" });
  let publicValueJudgment = ["failed", "blocked"].includes(status)
    ? { judgment: "unverified", preliminary: true, ceiling_applied: false, reason: "run failed or blocked; no usable user-value verdict may be handed off" }
    : valueJudgment;
  let publicConfidence = ["failed", "blocked"].includes(status) ? "low" : confidenceLabel;

  // --- Regression comparison --------------------------------------------
  let regressionComparison = null;
  if (regressionState) {
    const inheritance = checkHypothesisInheritance(regressionState.previous, linked.hypotheses);
    const identity = checkHypothesisIdentity(regressionState.previous, linked.hypotheses);
    const thresholds = checkThresholdIntegrity(vetted.plans, regressionState.previous);
    // Inspect the worker proposal before evidence-derived state normalization;
    // otherwise an attempted silent reopen can disappear from the audit trail.
    const settledReopen = checkSettledReopen(regressionState.previous, collected.hypotheses, vetted.plans);
    const ledger = buildLedger(regressionState.previous, linked.hypotheses);
    const currentScoringVersion = input.runtime?.scoring_schema_version ?? "0.1";
    const previousScoringVersion = regressionState.previous.scoring_schema_version ?? "0.1";
    const standardChanged =
      thresholds.changes.length > 0 ||
      identity.reframes.length > 0 ||
      previousScoringVersion !== currentScoringVersion;

    // R-02 / R-05 violations are a REGRESSION-DISCIPLINE failure, not a schema
    // failure: the envelope is well-formed, but the round is not comparable to
    // the previous one (dropped hypotheses, or a success threshold moved with no
    // stated reason). Reporting this as invalid_output_schema would hide the
    // real problem — "标准被静默替换" — behind a serialization complaint.
    if (inheritance.missing_ids.length > 0 || inheritance.invalid_new_ids.length > 0 || identity.violations.length > 0 || thresholds.violations.length > 0 || settledReopen.length > 0) {
      status = "failed";
      failureReason = "script_mismatch";
      retryable = true;
      needsHumanReview = true;
    }

    regressionComparison = {
      previous_task_id: regressionState.previous.task_id,
      previous_product_version: regressionState.previous.product_version,
      product_tasks_hash_match: regressionState.baseline.match,
      scoring_schema_version_match: previousScoringVersion === currentScoringVersion,
      standard_changed: standardChanged,
      standard_change_reasons: [
        ...thresholds.changes,
        ...identity.reframes.map((entry) => ({ item: `hypothesis_statement:${entry.hypothesis_id}`, from: entry.from, to: entry.to, reason: entry.reason })),
        ...(previousScoringVersion !== currentScoringVersion ? [{ item: "scoring_schema_version", from: previousScoringVersion, to: currentScoringVersion, reason: "runtime scoring schema version differs from previous round" }] : []),
      ],
      hypothesis_ledger: ledger,
      settled: inheritance.settled_ids.map((id) => ({
        hypothesis_id: id,
        verdict: "carried_settled",
        evidence_level: regressionState.previous.hypotheses.find((hypothesis) => hypothesis.hypothesis_id === id)?.evidence_level ?? "E0",
      })),
      still_open: linked.hypotheses
        .filter((hypothesis) => hypothesis.status === "open")
        .map((hypothesis) => ({
          hypothesis_id: hypothesis.hypothesis_id,
          why_still_unknown: hypothesis.deferred_reason ?? "validation plan not yet executed",
          blocking_input: hypothesis.linked_plan_ids.length > 0 ? "human execution of the linked plan" : "no plan linked",
        })),
      newly_added: linked.hypotheses
        .filter((hypothesis) => !hypothesis.carried_from_previous)
        .map((hypothesis) => ({ hypothesis_id: hypothesis.hypothesis_id, reason_for_addition: "surfaced in this round" })),
      task_comparison: compareTasks(regressionState.previous.task_results ?? [], collected.taskTests),
      persona_drift: comparePersonas(regressionState.previous.personas_digest ?? [], collected.personas),
      unresolved_from_previous: inheritance.missing_ids.map((id) => ({
        issue_id: id,
        status: "dropped",
        why_not_addressed: "hypothesis was not carried forward — contract violation (R-02)",
      })),
      progress_verdict: progressVerdict({
        baselineMatch: regressionState.baseline.match,
        standardChanged,
        ledger,
      }),
    };

    if (inheritance.missing_ids.length > 0) {
      executionLog.push(
        logEntry({
          step_id: "s5",
          outcome: "failed",
          detail: `Previous open hypotheses not carried forward: ${inheritance.missing_ids.join(", ")} (R-02).`,
        }),
      );
    }
    for (const id of inheritance.invalid_new_ids) executionLog.push(logEntry({ step_id: "s5", outcome: "failed", detail: `New hypothesis id ${id} collides with or precedes previous max id (R-03).` }));
    for (const violation of identity.violations) executionLog.push(logEntry({ step_id: "s5", outcome: "failed", detail: `${violation.hypothesis_id}: ${violation.problem} (R-03 identity).` }));
    for (const violation of settledReopen) executionLog.push(logEntry({ step_id: "s6", outcome: "failed", detail: `${violation.hypothesis_id}: ${violation.problem} (R-04).` }));
    for (const violation of thresholds.violations) {
      executionLog.push(
        logEntry({
          step_id: "s6",
          outcome: "failed",
          detail: `${violation.plan_id}: ${violation.problem} (R-05).`,
        }),
      );
    }
  }

  // Regression discipline is a late gate. Re-apply the public mask after all
  // gates have finalized status so stale medium/high values cannot escape.
  if (["failed", "blocked"].includes(status)) {
    publicValueJudgment = {
      judgment: "unverified",
      preliminary: true,
      ceiling_applied: false,
      reason: "run failed or blocked; no usable user-value verdict may be handed off",
    };
    publicConfidence = "low";
  }

  executionLog.push(
    logEntry({
      step_id: "s7_synthesis",
      outcome: "completed",
      detail:
        `Issued cards ${evidenceCards.length}, ingested records ${ingestedRecords.length}, ` +
        `max tier ${maxTier(allEvidence())}, real-user evidence ${realEvidence}. ` +
        `Uncounted dimensions ${uncountedCount(dimensions)}. user_value_judgment ${publicValueJudgment.judgment}` +
        `${publicValueJudgment.ceiling_applied ? " (ceiling applied)" : ""}. Plans published ${["failed", "blocked"].includes(status) ? 0 : vetted.plans.length}, rejected ${vetted.rejected.length}.`,
      kb_ids: kbIdsFor("scoring"),
    }),
  );

  const safeFailure = ["failed", "blocked"].includes(status);
  const publishedDimensions = safeFailure ? emptyDimensions() : dimensions;
  const publishedTotals = safeFailure ? scoreTotals(publishedDimensions) : totals;
  const publishedPlans = safeFailure ? [] : vetted.plans;
  const applicableEvidenceIds = new Set(evidenceEffectLedger.filter((entry) =>
    ["support", "contradict"].includes(entry.relation) && entry.scope_valid && entry.product_version_valid && entry.semantic_valid,
  ).map((entry) => entry.evidence_id));
  const applicableEvidence = allEvidence().filter((entry) => applicableEvidenceIds.has(entry.evidence_id));

  const structured = {
    ...emptyStructuredOutput(),
    simulation_disclaimer: SIMULATION_DISCLAIMER,
    overall_judgment: toOverallJudgment(publicValueJudgment.judgment),
    user_value_judgment: publicValueJudgment.judgment,
    evidence_confidence: publicConfidence,
    user_value_score: {
      raw_total: publishedTotals.raw_total,
      counted_weight: publishedTotals.counted_weight,
      normalized_total: publishedTotals.normalized_total,
      uncounted_dimension_count: uncountedCount(publishedDimensions),
      dimensions: publishedDimensions,
      preliminary: publicValueJudgment.preliminary,
      evidence_ceiling: SIMULATION_CEILING,
      user_value_ceiling: {
        applied: publicValueJudgment.ceiling_applied,
        ceiling: publicValueJudgment.ceiling_applied ? "medium" : null,
        reason: publicValueJudgment.reason,
      },
      scoring_note: SCORING_NOTE,
    },
    target_user_definition: {
      admitted: true,
      breadth_check: {
        verdict: breadth.verdict,
        matched_broad_patterns: breadth.matched_broad_patterns,
        reason: breadth.reason,
      },
      converged_segments: collected.segments ?? [],
      excluded_segments: exclusionsFromInput(input.target_users?.exclusions),
      clarification_questions: breadth.clarification_questions,
    },
    personas: collected.personas,
    persona_outcomes: buildPersonaOutcomes(collected.personas, collected.taskTests),
    persona_set_check: personaCheck,
    jobs_to_be_done: collected.jobs,
    scenarios_and_alternatives: collected.scenarios,
    simulated_findings: {
      executed: {
        first_experience: collected.firstExperience.length > 0,
        task_test: collected.taskTests.length > 0,
      },
      skip_reasons: skipReasons,
      evidence_tier: "E2",
      first_experience: collected.firstExperience,
      value_communication_failure: flags.value_communication_failure,
      task_test_matrix: collected.taskTests,
      experience_issues: collected.experienceIssues ?? [],
      simulated_interview: collected.interview,
      hidden_needs: collected.hiddenNeeds,
      insights: collected.insights,
      politeness_feedback_removed: collected.politenessRemoved,
      realism_check: checkRealism({
        negativeFindings: collected.negativeFindings,
        hiddenNeeds: collected.hiddenNeeds.length,
        retriesUsed: realismRetries,
        executed: !skipReasons.some((skip) => skip.unit === "s5"),
        interviews: collected.interview,
        personaIds: collected.personas.map((persona) => persona.persona_id),
      }),
    },
    user_hypotheses: linked.hypotheses,
    top_user_problems: buildTopProblems(linked.hypotheses, collected),
    validation_plans: publishedPlans,
    deferred_validations: linked.hypotheses
      .filter((hypothesis) => hypothesis.linked_plan_ids.length === 0 && hypothesis.deferred_reason)
      .map((hypothesis) => ({
        hypothesis_id: hypothesis.hypothesis_id,
        reason: hypothesis.deferred_reason,
        revisit_condition: hypothesis.revisit_condition ?? "next validation round",
      })).concat((vetted.deferred ?? []).filter((entry) => !linked.hypotheses.some((hypothesis) => hypothesis.hypothesis_id === entry.hypothesis_id && hypothesis.deferred_reason)).map((entry) => ({
        hypothesis_id: entry.hypothesis_id,
        reason: `constraint_gap: ${entry.reason}`,
        revisit_condition: "constraints change or a smaller leading-indicator plan is approved",
      }))),
    evidence_level_summary: {
      max_tier_achieved: maxTier(allEvidence()),
      max_ingested_tier: maxTier(ingestedRecords),
      max_applicable_tier: maxTier(applicableEvidence),
      has_real_user_evidence: realEvidence,
      tier_distribution: tierDistribution(allEvidence()),
      ingested_tier_distribution: tierDistribution(ingestedRecords),
      applicable_tier_distribution: tierDistribution(applicableEvidence),
      simulation_capped: true,
      downgraded_entries: downgraded,
      expiry_unknown_refs: ingestedRecords.filter((record) => record.expiry === "unknown").map((record) => record.evidence_id),
      per_claim: perClaim,
      judgment_ceiling: {
        applied: publicValueJudgment.ceiling_applied,
        ceiling: publicValueJudgment.ceiling_applied ? "medium" : publicValueJudgment.judgment === "unverified" ? "unverified" : null,
        reason: publicValueJudgment.reason,
      },
    },
    evidence_effect_ledger: evidenceEffectLedger,
    missing_information: missingInformation,
    conflicts,
    critical_issue: buildCriticalIssue({ executedAny, valueJudgment: publicValueJudgment, flags, linked, collected }),
    flags,
    out_of_scope_redirects: objectiveScope.redirects,
    evidence_cards: evidenceCards,
    regression_comparison: regressionComparison,
    run_manifest: {
      product_tasks_hash: computedTaskHash,
      scoring_schema_version: input.runtime?.scoring_schema_version ?? "0.1",
      skill_version: "1.0.5",
      task_id: input.task_id,
      project_id: input.project_id,
      mode,
      effective_retry_limit: effectiveRetries,
      product_version: input.product_version,
      capabilities: availability,
      standard_change_reason: regressionComparison?.standard_change_reasons?.map((item) => item.reason).join("; ") || null,
      state_hash: "0".repeat(64),
      upstream_evidence_refs: resolvedUpstreamEvidenceRefs(input),
    },
    integrity_diagnostics: integrityDiagnostics,
    rejected_output: [],
    execution_log: executionLog,
  };
  structured.handoff = buildHandoff(structured, {
    ingestedEvidence: ingestedRecords,
    judgmentEvidenceRefs: [...judgmentEvidenceIds],
    failureSafe: safeFailure,
  });
  structured.summary_report = renderSummaryReport({ input, structured, ingestedEvidence: ingestedRecords });
  structured.summary_report_html = renderSummaryReportHtml({ input, structured, ingestedEvidence: ingestedRecords });
  structured.full_report = renderFullReport({ input, structured, ingestedEvidence: ingestedRecords });
  structured.full_report_html = renderFullReportHtml({ input, structured, ingestedEvidence: ingestedRecords });
  // Backward compatibility: historical human_report fields remain the default
  // concise presentation layer.
  structured.human_report = structured.summary_report ?? renderHumanReport({ input, structured, ingestedEvidence: ingestedRecords });
  structured.human_report_html = structured.summary_report_html ?? renderHumanReportHtml({ input, structured, ingestedEvidence: ingestedRecords });
  structured.run_manifest.state_hash = computeStateHash(structured);

  const result = {
    task_id: input.task_id,
    status,
    result_summary: buildSummary({ valueJudgment: publicValueJudgment, confidenceLabel: publicConfidence, status, skipReasons, executedAny, plans: publishedPlans }),
    structured_output: structured,
    evidence_refs: [...new Set([...evidenceCards.map((card) => card.evidence_id), ...judgmentEvidenceIds])],
    confidence: publicConfidence === "high" ? 0.8 : publicConfidence === "medium" ? 0.5 : 0.2,
    risks: buildRisks(flags, skipReasons, vetted),
    needs_human_review: needsHumanReview,
    failure_reason: failureReason,
    retryable,
  };

  // --- A-26: self-check the assembled output ----------------------------
  const outputCheck = validate(result, schemas.output, registry);
  if (!outputCheck.valid) {
    const preserved = {
      ...structured,
      overall_judgment: "insufficient_evidence",
      user_value_judgment: "unverified",
      evidence_confidence: "low",
      user_hypotheses: [],
      validation_plans: [],
      deferred_validations: [],
      rejected_output: outputCheck.errors.map((error) => ({ path: error.path, reason: error.message })),
    };
    preserved.handoff = buildHandoff(preserved, { ingestedEvidence: ingestedRecords, judgmentEvidenceRefs: [], failureSafe: true });
    const failed = {
      ...result,
      status: "failed",
      result_summary: "Internal output validation failed; valid earlier evidence and audit logs were preserved while invalid late-step sections were quarantined.",
      structured_output: preserved,
      confidence: 0.2,
      needs_human_review: true,
      failure_reason: "invalid_output_schema",
      retryable: false,
      risks: outputCheck.errors.map((error) => `${error.path}: ${error.message}`),
    };
    const preservedCheck = validate(failed, schemas.output, registry);
    if (preservedCheck.valid) return failed;
    return blockedResult(input.task_id, "invalid_output_schema", "Internal output validation failed; valid evidence cards and execution log were preserved.", {
      status: "failed",
      retryable: false,
      needs_human_review: true,
      risks: preservedCheck.errors.map((error) => `${error.path}: ${error.message}`),
      execution_log: executionLog,
      structured_output: { evidence_cards: evidenceCards, rejected_output: outputCheck.errors.map((error) => ({ path: error.path, reason: error.message })) },
    });
  }
  return result;
}

// --- helpers ---------------------------------------------------------------

/** A-04 / A-05: per-step input preconditions. */
function gateStep(step, input, availability, stepStates = new Map()) {
  const dependencies = {
    s3: ["s2"],
    s4a: ["s3"],
    s4b: ["s4a"],
    s5: ["s2", "s3"],
    s6: ["s1", "s2", "s3", "s5"],
  };
  const failedDependencies = (dependencies[step.id] ?? []).filter((id) => !["completed", "partial"].includes(stepStates.get(id)));
  if (failedDependencies.length > 0) {
    return { executable: false, reason: `Dependency failed or did not execute: ${failedDependencies.join(", ")}.`, missing: "dependency_failed" };
  }
  const unavailable = step.capabilities.filter((capability) => availability[capability] !== "available");
  if (unavailable.length > 0) {
    return {
      executable: false,
      reason: `Required capability unavailable: ${unavailable.join(", ")}.`,
      missing: unavailable.join(","),
    };
  }
  const hasSurface = Boolean(input.product_profile?.url) || Boolean(input.product_profile?.experience_report_ref);

  if (step.id === "s4a" && !hasSurface) {
    return {
      executable: false,
      reason: "No product url and no upstream experience report: first-experience simulation has nothing to observe.",
      missing: "product_profile.url|product_profile.experience_report_ref",
    };
  }
  if (step.id === "s4b") {
    if (!Array.isArray(input.product_tasks) || input.product_tasks.length === 0) {
      return {
        executable: false,
        reason: "No product_tasks supplied: task testing is skipped and no task result may be reported (KB-USR-R04).",
        missing: "product_tasks",
      };
    }
    if (!hasSurface) {
      return {
        executable: false,
        reason: "No product surface to execute tasks against.",
        missing: "product_profile.url|product_profile.experience_report_ref",
      };
    }
  }
  return { executable: true, reason: "preconditions met", missing: null };
}

function mergeOutcome(collected, outcome) {
  const append = (key, value) => {
    if (Array.isArray(value)) collected[key] = [...(collected[key] ?? []), ...value];
  };
  append("personas", outcome.personas);
  append("jobs", outcome.jobs);
  append("scenarios", outcome.scenarios);
  append("firstExperience", outcome.firstExperience);
  append("taskTests", outcome.taskTests);
  append("interview", outcome.interview);
  append("hiddenNeeds", outcome.hiddenNeeds);
  append("insights", outcome.insights);
  append("politenessRemoved", outcome.politenessRemoved);
  append("hypotheses", outcome.hypotheses);
  append("plans", outcome.plans);
  append("experienceIssues", outcome.experienceIssues);
  append("conflictCandidates", outcome.conflictCandidates);
  if (Array.isArray(outcome.segments)) collected.segments = [...(collected.segments ?? []), ...outcome.segments];
  if (typeof outcome.negativeFindings === "number") {
    collected.negativeFindings += outcome.negativeFindings;
  }
}

/** KB-USR-S3 分支: >=2 personas unable to restate the value claim. */
function computeValueCommunicationFailure(firstExperience) {
  const failures = (firstExperience ?? []).filter((entry) => entry.can_restate_value === false).length;
  return failures >= 2;
}

function normalizePersonaPainPriority(personas) {
  return (personas ?? []).map((persona) => ({
    ...persona,
    pains: (persona.pains ?? []).map((pain) => ({
      ...pain,
      priority_score: (pain.frequency ?? 0) * (pain.severity ?? 0) * (pain.workaround_cost ?? 0),
    })),
  }));
}

function jobCoverageDiagnostics(personas, jobs) {
  const expected = new Set((personas ?? []).map((persona) => persona.persona_id));
  const covered = new Set((jobs ?? []).flatMap((job) => job.persona_ids ?? []));
  const missing = [...expected].filter((id) => !covered.has(id));
  return { valid: missing.length === 0, reason: missing.length === 0 ? "every Persona is covered by at least one JTBD" : `JTBD missing Persona(s): ${missing.join(", ")}` };
}

function scenarioResearchIntegrity(scenarios, personas) {
  const requiredStages = ["awareness", "trial", "first_use", "continued_use", "referral"];
  const reasons = [];
  for (const scenario of scenarios ?? []) {
    if (!(scenario.alternatives ?? []).some((alternative) => alternative.alternative_type === "do_nothing")) {
      reasons.push(`${scenario.persona_id} has no do_nothing alternative`);
    }
    const stages = (scenario.journey ?? []).map((entry) => entry.stage);
    const complete = requiredStages.every((stage) => stages.filter((value) => value === stage).length === 1) && stages.length === requiredStages.length;
    if (!complete) reasons.push(`${scenario.persona_id} journey must contain each five-stage value exactly once`);
  }
  const personaById = new Map((personas ?? []).map((persona) => [persona.persona_id, persona]));
  const fingerprints = (scenarios ?? []).map((scenario) => JSON.stringify({
    alternatives: (scenario.alternatives ?? []).map((alternative) => alternative.alternative_type).sort(),
    switching: scenario.switching_forces?.verdict ?? null,
    rejection: [...(personaById.get(scenario.persona_id)?.rejection_reasons ?? [])].map((value) => String(value).normalize("NFC").toLowerCase()).sort(),
    constraints: scenario.limits ?? null,
    outcome: (scenario.journey ?? []).map((stage) => `${stage.stage}:${stage.drop_off_risk}`),
  }));
  if (fingerprints.length >= 3 && new Set(fingerprints).size === 1) reasons.push("Persona scenarios are semantically homogeneous across alternatives, switching, rejection, constraints and outcomes");
  return { valid: reasons.length === 0, reasons };
}

function normalizeSimulationFactTypes(collected, realEvidence) {
  const realIds = new Set((realEvidence ?? []).filter((record) => record.scope_valid === true && hasRealUserEvidence([record])).map((record) => record.evidence_id));
  const visit = (value) => {
    if (Array.isArray(value)) return value.forEach(visit);
    if (!value || typeof value !== "object") return;
    if (Object.prototype.hasOwnProperty.call(value, "fact_type")) {
      const refs = [...(value.evidence_refs ?? []), ...(value.supporting_refs ?? [])];
      if (!refs.some((ref) => realIds.has(ref))) value.fact_type = value.fact_type === "assumption" ? "assumption" : "inference";
    }
    Object.values(value).forEach(visit);
  };
  for (const key of ["personas", "jobs", "segments", "scenarios", "firstExperience", "taskTests", "interview", "hiddenNeeds", "insights", "experienceIssues"]) visit(collected[key]);
}

function applyTechnicalAttributionGate(collected, input) {
  const upstream = input.upstream_product_handoff?.blocking_observations ?? [];
  const globalRefs = new Set(resolvedUpstreamEvidenceRefs(input));
  const supportedTask = (task) => upstream.some((observation) => {
    const refs = observation.evidence_refs ?? [];
    const traceable = refs.some((ref) => globalRefs.has(ref)) || (observation.observation_id && globalRefs.has(observation.observation_id));
    return traceable && observation.task_key === task.task_key && ["functional", "performance"].includes(observation.technical_attribution);
  });
  collected.taskTests = (collected.taskTests ?? []).map((task) =>
    ["functional", "performance"].includes(task.cause_type) && !supportedTask(task) ? { ...task, cause_type: "unknown" } : task,
  );
  collected.experienceIssues = (collected.experienceIssues ?? []).map((issue) => {
    if (!["functional", "performance"].includes(issue.cause_type)) return issue;
    const relatedTasks = (collected.taskTests ?? []).filter((task) =>
      (task.evidence_refs ?? []).some((ref) => (issue.evidence_refs ?? []).includes(ref)),
    );
    const traceable = (issue.evidence_refs ?? []).some((ref) => globalRefs.has(ref)) || upstream.some((observation) => {
      const hasTrace = (observation.evidence_refs ?? []).some((ref) => globalRefs.has(ref));
      const taskMatches = relatedTasks.some((task) => task.task_key === observation.task_key);
      return hasTrace && (taskMatches || String(issue.step_ref ?? "").includes(String(observation.task_key ?? "")));
    });
    return traceable ? issue : { ...issue, cause_type: "unknown" };
  });
}

function enforceExperienceIssueExecutionGate(collected, { stepStates, ingestedRecords, evidenceCards, input, diagnostics = [] }) {
  const taskExecuted = stepStates.get("s4b") === "completed";
  if (taskExecuted) return;

  const realEvidenceIds = new Set(
    (ingestedRecords ?? [])
      .filter((record) => hasRealUserEvidence([record]) && record.integrity_valid !== false)
      .map((record) => record.evidence_id),
  );
  const upstreamIds = new Set(resolvedUpstreamEvidenceRefs(input));
  const firstExperienceIds = new Set(
    (evidenceCards ?? [])
      .filter((card) => card.evidence_type === "simulated_experience_evidence")
      .map((card) => card.evidence_id),
  );
  const firstExperienceExecuted = stepStates.get("s4a") === "completed";

  const kept = [];
  for (const issue of collected.experienceIssues ?? []) {
    const refs = issue.evidence_refs ?? [];
    const backedByRealOrUpstream = refs.some((ref) => realEvidenceIds.has(ref) || upstreamIds.has(ref));
    const backedByFirstExperience = firstExperienceExecuted &&
      ["cognitive", "content", "unknown"].includes(issue.cause_type) &&
      refs.some((ref) => firstExperienceIds.has(ref));

    if (backedByRealOrUpstream || backedByFirstExperience) {
      kept.push(issue);
      continue;
    }
    diagnostics.push({
      code: "unobserved_experience_issue_removed",
      ref: issue.issue_id ?? null,
      detail: "Experience issue had no executed task-test evidence, no caller-supplied real-user evidence, and no trusted upstream product evidence; it was removed from user-visible and downstream handoff output.",
    });
  }
  collected.experienceIssues = kept;
}

function resolvedUpstreamEvidenceRefs(input) {
  const declared = new Set(input?.evidence_refs ?? []);
  const cardIds = new Set((input?.upstream_product_handoff?.product_evidence_cards ?? [])
    .filter(isCanonicalProductEvidenceCard)
    .map((card) => card?.evidence_id)
    .filter(Boolean));
  const trusted = new Set([...declared, ...cardIds]);
  const resolved = [...trusted];
  for (const observation of input?.upstream_product_handoff?.blocking_observations ?? []) {
    for (const ref of observation.evidence_refs ?? []) if (trusted.has(ref)) resolved.push(ref);
    if (observation.observation_id && trusted.has(observation.observation_id)) resolved.push(observation.observation_id);
  }
  return [...new Set(resolved)];
}

function isCanonicalProductEvidenceCard(card) {
  if (!card || typeof card !== "object") return false;
  const required = ["evidence_id", "evidence_type", "source", "source_tier", "timestamp", "reliability_level", "supporting_claims", "applicability", "expiry", "content_hash", "observation", "fact_type"];
  return required.every((field) => Object.prototype.hasOwnProperty.call(card, field)) &&
    /^EV-[A-Za-z0-9._-]+$/u.test(card.evidence_id) && /^[a-f0-9]{64}$/u.test(card.content_hash) &&
    ["untraceable", "tier_3", "tier_2", "tier_1"].includes(card.source_tier) &&
    ["E0", "E1", "E2", "E3", "E4", "E5"].includes(card.reliability_level) &&
    Array.isArray(card.supporting_claims) && card.applicability && typeof card.applicability === "object";
}

function hasMaterialComplianceConcern(notes) {
  const normalized = String(notes ?? "").normalize("NFC").trim().toLowerCase().replace(/[\s，。,.!！]+/gu, "");
  if (!normalized || ["无", "none", "无特别要求", "无特别合规要求", "无特殊要求", "无特殊合规要求", "n/a", "na"].includes(normalized)) return false;
  return true;
}

function applyEvidenceEffectsToDimensions(dimensions, hypotheses, ledger, evidence) {
  const result = structuredClone(dimensions);
  const byId = new Map((evidence ?? []).map((record) => [record.evidence_id, record]));
  for (const [dimensionKey, dimension] of Object.entries(result)) {
    const claimIds = new Set((hypotheses ?? []).filter((claim) => (claim.affected_dimensions ?? []).includes(dimensionKey)).map((claim) => claim.hypothesis_id));
    const realEffects = (ledger ?? []).filter((entry) =>
      claimIds.has(entry.claim_id) && ["support", "contradict"].includes(entry.relation) && entry.scope_valid && entry.product_version_valid && entry.semantic_valid && ["E3", "E4", "E5"].includes(entry.effective_tier),
    );
    if (realEffects.length === 0) continue;
    const refs = [...new Set(realEffects.map((entry) => entry.evidence_id).filter((id) => byId.has(id)))];
    result[dimensionKey] = {
      ...dimension,
      score: null,
      counted: false,
      cap_reason: "real_evidence_requires_rescore",
      evidence_refs: refs,
      basis: `Applicable real evidence overrides the simulated ordinal for ${dimensionKey}; no replacement number is invented. Narrow evidence interpretation/rescoring is required.`,
      max_tier: maxTier(refs.map((ref) => byId.get(ref))),
      needs_rescore: true,
      real_evidence_overrides_simulation: true,
    };
  }
  return result;
}

function buildEvidenceRelationConflicts(hypotheses, ingestedRecords, issuedCards) {
  const issuedById = new Map((issuedCards ?? []).map((card) => [card.evidence_id, card]));
  const candidates = [];
  for (const hypothesis of hypotheses ?? []) {
    const affected = new Set(hypothesis.affected_dimensions ?? []);
    const simulatedRefs = [...new Set([
      ...(hypothesis.supporting_refs ?? []).filter((ref) => issuedById.has(ref)),
      ...(issuedCards ?? [])
        .filter((card) => (card.applicability?.valid_for_dimensions ?? []).some((key) => affected.has(key)))
        .map((card) => card.evidence_id),
    ])];
    const realRefs = (hypothesis.contradicting_refs ?? []).filter((ref) =>
      (ingestedRecords ?? []).some((record) => record.evidence_id === ref && ["E3", "E4", "E5"].includes(record.reliability_level)),
    );
    for (const realRef of realRefs) {
      for (const simulatedRef of simulatedRefs) {
        candidates.push({
          conflict_id: `CF-${hypothesis.hypothesis_id}-${realRef}-${simulatedRef}`.replace(/[^A-Za-z0-9._-]/gu, "-"),
          conflict_type: "simulation_vs_real",
          side_a: { ref: realRef, statement: `Real evidence contradicts ${hypothesis.hypothesis_id}` },
          side_b: { ref: simulatedRef, statement: `Simulation previously supported ${hypothesis.hypothesis_id}` },
        });
      }
    }
  }
  return candidates;
}

function buildRealEvidenceConflicts(hypotheses, ingestedRecords) {
  const candidates = [];
  for (const hypothesis of hypotheses ?? []) {
    const support = (hypothesis.supporting_refs ?? []).filter((ref) => (ingestedRecords ?? []).some((record) => record.evidence_id === ref && hasRealUserEvidence([record])));
    const contradict = (hypothesis.contradicting_refs ?? []).filter((ref) => (ingestedRecords ?? []).some((record) => record.evidence_id === ref && hasRealUserEvidence([record])));
    for (const supportRef of support) {
      for (const contradictRef of contradict) {
        candidates.push({
          conflict_id: `CF-REAL-${hypothesis.hypothesis_id}-${supportRef}-${contradictRef}`.replace(/[^A-Za-z0-9._-]/gu, "-"),
          conflict_type: "real_vs_real",
          side_a: { ref: supportRef, statement: `Real evidence supports ${hypothesis.hypothesis_id}` },
          side_b: { ref: contradictRef, statement: `Real evidence contradicts ${hypothesis.hypothesis_id}` },
        });
      }
    }
  }
  return candidates;
}

function buildConflicts(collected, evidenceCards, { diagnostics = [], productVersion = null } = {}) {
  const conflicts = [];
  const registry = new Map((evidenceCards ?? []).map((entry) => [entry.evidence_id, entry]));
  for (const candidate of collected.conflictCandidates ?? []) {
    const resolveSide = (side) => {
      const evidence = registry.get(side?.ref);
      const productMatches = evidence?.version_stable === true || evidence?.applicability?.product_version === productVersion;
      if (!evidence || evidence.integrity_valid === false || evidence.scope_valid === false || !productMatches) return null;
      return {
        ref: evidence.evidence_id,
        tier: evidence.reliability_level,
        statement: String(side?.statement ?? evidence.observation ?? "Evidence relation").slice(0, 500),
      };
    };
    const sideA = resolveSide(candidate.side_a);
    const sideB = resolveSide(candidate.side_b);
    if (!sideA || !sideB) {
      diagnostics.push({
        code: "unknown_reference",
        ref: candidate.conflict_id ?? null,
        detail: "conflict candidate references missing, invalid, out-of-scope, or wrong-version evidence; no resolution was issued",
      });
      continue;
    }
    const resolution = resolveConflict(sideA, sideB);
    conflicts.push({
      conflict_id: candidate.conflict_id,
      conflict_type: candidate.conflict_type ?? candidate.type,
      side_a: sideA,
      side_b: sideB,
      ...resolution,
      note:
        resolution.resolution === "real_evidence_wins"
          ? "Real user evidence (E3+) prevails; the simulated conclusion is retained as reference_only. Values are never averaged (KB-USR-B04)."
          : "Both sides retained for the supervisor to surface as a key contradiction; not averaged.",
    });
  }
  return conflicts;
}

function buildTopProblems(hypotheses, collected) {
  const severityOrder = { blocker: 0, major: 1 };
  const issueProblems = (collected?.experienceIssues ?? [])
    .filter((issue) => Object.prototype.hasOwnProperty.call(severityOrder, issue.severity))
    .sort((a, b) => severityOrder[a.severity] - severityOrder[b.severity])
    .slice(0, 5)
    .map((issue, index) => ({
      problem_id: `UP-I${index + 1}`,
      question: issue.description,
      why_it_matters: `A ${issue.severity} experience issue blocks or materially degrades the user task.`,
      blocks_which_judgment: "user value judgment",
      related_hypothesis_ids: [],
      related_issue_ids: [issue.issue_id],
      rank: index + 1,
    }));
  const functional = (collected?.taskTests ?? [])
    .filter((task) => task.result === "failed" && task.cause_type === "functional")
    .filter((task) => !(collected?.experienceIssues ?? []).some((issue) =>
      (issue.evidence_refs ?? []).some((ref) => (task.evidence_refs ?? []).includes(ref)) ||
      String(issue.step_ref ?? "").normalize("NFC").includes(String(task.task_key ?? "").normalize("NFC")),
    ))
    .slice(0, Math.max(0, 5 - issueProblems.length))
    .map((task, index) => ({
      problem_id: `UP-F${index + 1}`,
      question: task.abandon_reason ?? `Functional core task ${task.task_key} failed for ${task.persona_id}`,
      why_it_matters: "A functional core-task blocker forces the affected Persona verdict to reject.",
      blocks_which_judgment: "user value judgment",
      related_hypothesis_ids: [],
      related_issue_ids: (collected.experienceIssues ?? []).filter((issue) => issue.cause_type === "functional").map((issue) => issue.issue_id),
      rank: issueProblems.length + index + 1,
    }));
  const hypothesisProblems = hypotheses
    .filter((hypothesis) => ["open", "partially_validated"].includes(hypothesis.status))
    .slice(0, Math.max(0, 5 - issueProblems.length - functional.length))
    .map((hypothesis, index) => ({
      problem_id: `UP${index + 1}`,
      question: hypothesis.statement,
      why_it_matters: `Unresolved at ${hypothesis.current_evidence_level ?? "E0"}; affects ${
        (hypothesis.affected_dimensions ?? []).join(", ") || "no scored dimension"
      }.`,
      blocks_which_judgment: hypothesis.decision_impact === "blocking" ? "user value judgment" : "dimension scoring",
      related_hypothesis_ids: [hypothesis.hypothesis_id],
      related_issue_ids: (collected.experienceIssues ?? [])
        .filter((issue) => (hypothesis.related_issue_ids ?? []).includes(issue.issue_id))
        .map((issue) => issue.issue_id),
      rank: issueProblems.length + functional.length + index + 1,
    }));
  return [...issueProblems, ...functional, ...hypothesisProblems];
}

function buildCriticalIssue({ executedAny, valueJudgment, flags, linked, collected }) {
  if (!executedAny) {
    return {
      issue: "No simulation capability is bound, so no user-side conclusion could be evidenced.",
      impact:
        "The skill cannot say whether users need this product. Any judgment reported now would rest on nothing.",
      recommendation: "Bind the simulation_engine and product_reader adapters, then re-run with the same product_tasks script.",
      evidence_refs: [],
    };
  }
  if (flags.persona_homogeneous) {
    return {
      issue: "The persona set is not differentiated: at least two personas share almost all behaviour keys.",
      impact: "Undifferentiated personas cannot reveal who rejects the product or why, so every downstream conclusion is unreliable.",
      recommendation: "Re-model personas with distinct alternatives and budget constraints (KB-USR-G02).",
      evidence_refs: [],
    };
  }
  if (flags.simulation_unrealistic) {
    return {
      issue: "The simulation produced no complaints or no hidden needs, which real users never do.",
      impact: "A simulation without negatives systematically overstates user value.",
      recommendation: "Re-run the interview simulation with stricter persona constraints (KB-USR-B02).",
      evidence_refs: [],
    };
  }
  const severityOrder = { blocker: 3, major: 2, minor: 1 };
  const topIssue = [...(collected?.experienceIssues ?? [])].sort(
    (a, b) => (severityOrder[b.severity] ?? 0) - (severityOrder[a.severity] ?? 0),
  )[0];
  if (["blocker", "major"].includes(topIssue?.severity)) {
    return {
      issue: topIssue.description,
      impact: `A ${topIssue.severity} user-task issue prevents a core workflow from completing for ${topIssue.frequency_persona_count} Persona(s).`,
      recommendation: `Resolve and re-test ${topIssue.step_ref ?? topIssue.issue_id} before treating the user review as complete.`,
      evidence_refs: topIssue.evidence_refs ?? [],
    };
  }
  const failedTask = (collected?.taskTests ?? []).find((task) => task.result === "failed");
  if (failedTask) {
    return {
      issue: failedTask.abandon_reason ?? `Core task ${failedTask.task_key} failed for ${failedTask.persona_id}.`,
      impact: "A highest-severity task failure blocks the affected Persona from realizing the tested value.",
      recommendation: `Fix the ${failedTask.cause_type ?? "unknown"} failure and repeat ${failedTask.task_key} for ${failedTask.persona_id}.`,
      evidence_refs: failedTask.evidence_refs ?? [],
    };
  }
  const blockingHypothesis = linked.hypotheses.find(
    (hypothesis) => hypothesis.priority_rank === 1 && hypothesis.decision_impact === "blocking" && hypothesis.status === "open",
  );
  if (blockingHypothesis) {
    return {
      issue: blockingHypothesis.statement,
      impact: "The highest-priority blocking user hypothesis remains unresolved.",
      recommendation: "Execute its linked validation plan before raising confidence in the user-value judgment.",
      evidence_refs: [
        ...(blockingHypothesis.supporting_refs ?? []),
        ...(blockingHypothesis.contradicting_refs ?? []),
      ],
    };
  }
  if (valueJudgment.judgment === "unverified") {
    return {
      issue: "Three or more scoring dimensions lack evidence above team self-report.",
      impact: "No strength claim can be made about user value; only validation plans are meaningful output now.",
      recommendation: `Execute the ${linked.hypotheses.length > 0 ? "linked validation plans" : "designed validation plans"} to raise evidence above E2.`,
      evidence_refs: [],
    };
  }
  if (valueJudgment.ceiling_applied) {
    return {
      issue: "No E3+ real-user evidence exists, so user value is capped at medium regardless of simulated score.",
      impact: "The current judgment is preliminary and rests on E2 simulation only.",
      recommendation: "Execute the highest-priority validation plan to obtain real-user evidence.",
      evidence_refs: [],
    };
  }
  return null;
}

function derivePersonaConfidence(persona, ingestedRecords) {
  const proposal = structuredClone(persona);
  const provenance = { ...(proposal?.field_provenance ?? {}) };
  const applicable = (ingestedRecords ?? []).filter(
    (record) =>
      hasRealUserEvidence([record]) &&
      record.scope_valid === true &&
      record.integrity_valid !== false &&
      record.applicability?.persona_ids?.includes(persona.persona_id) &&
      (!["interview", "survey", "usability_test"].includes(record.kind) || record.sample_adequacy === "adequate") &&
      (record.applicability?.valid_for_dimensions?.length ?? 0) > 0,
  );
  const fieldDimensions = {
    goal: new Set(["demand_strength", "usage_frequency"]),
    pains: new Set(["pain_severity"]),
    alternative: new Set(["alternative_gap"]),
    value_threshold: new Set(["demand_strength", "usage_frequency"]),
  };
  const coreCalibrating = [];
  for (const [field, dimensions] of Object.entries(fieldDimensions)) {
    const records = applicable.filter((record) => (record.applicability?.valid_for_dimensions ?? []).some((dimension) => dimensions.has(dimension)));
    if (records.length > 0) {
      provenance[field] = "fact";
      coreCalibrating.push(...records);
    }
  }
  const allCoreAssumptions = ["goal", "pains", "alternative"].every((field) => provenance[field] === "assumption");
  const calibratedRefs = [...new Set(coreCalibrating.map((record) => record.evidence_id))];
  const confidence = calibratedRefs.length > 0 ? "medium" : allCoreAssumptions ? "low" : proposal.confidence === "high" ? "medium" : (proposal.confidence ?? "low");
  return {
    ...proposal,
    field_provenance: provenance,
    confidence,
    eligible_for_scoring: confidence !== "low" && proposal.eligible_for_scoring !== false,
    calibrated_by_real_evidence: calibratedRefs,
  };
}

function validateEvidenceScopes(records, { personas, segments, diagnostics }) {
  const personaIds = new Set((personas ?? []).map((persona) => persona.persona_id));
  const segmentIds = new Set((segments ?? []).filter(Boolean));
  return (records ?? []).map((record) => {
    const scopedPersonas = record.applicability?.persona_ids ?? [];
    const personaValid = scopedPersonas.every((id) => personaIds.has(id));
    const segment = record.applicability?.segment;
    const segmentValid = !segment || segmentIds.has(segment);
    const scopeDefined = Boolean(segment) || scopedPersonas.length > 0;
    const scope_valid = personaValid && segmentValid && scopeDefined && record.integrity_valid !== false;
    if (!scope_valid) diagnostics.push({ code: "applicability_gap", ref: record.evidence_id, detail: !personaValid ? "unknown persona scope" : !segmentValid ? "segment scope does not match this run" : !scopeDefined ? "no Persona or segment scope; extrapolation forbidden" : "integrity/version scope invalid" });
    return { ...record, scope_valid };
  });
}

function attachApplicableRealEvidence(dimensions, ingestedRecords) {
  const result = structuredClone(dimensions);
  for (const [dimensionKey, dimension] of Object.entries(result)) {
    const applicable = (ingestedRecords ?? []).filter(
      (record) =>
        hasRealUserEvidence([record]) &&
        record.scope_valid === true &&
        record.integrity_valid !== false &&
        record.applicability?.valid_for_dimensions?.includes(dimensionKey) &&
        ((record.supporting_claims?.length ?? 0) > 0 || (record.contradicting_claims?.length ?? 0) > 0),
    );
    if (applicable.length === 0) continue;
    dimension.evidence_refs = [
      ...new Set([...(dimension.evidence_refs ?? []), ...applicable.map((record) => record.evidence_id)]),
    ];
    dimension.basis = `${dimension.basis ?? ""} Calibrated by applicable caller-supplied real evidence: ${applicable
      .map((record) => record.evidence_id)
      .join(", ")}. Sample adequacy: ${applicable.map((record) => `${record.evidence_id}=${record.sample_adequacy ?? "unknown"}`).join(", ")}.`.trim();
  }
  return result;
}

function normalizeTaskMatrix({ personas, tasks, records }) {
  const eligibleIds = (personas ?? [])
    .filter((persona) => persona.eligible_for_scoring === true)
    .map((persona) => persona.persona_id);
  const taskKeys = (tasks ?? []).map((task) => task.task_key);
  if (taskKeys.length === 0) return { records: [], incomplete: false, missing_pairs: [], reason: "No task script supplied." };
  if (eligibleIds.length === 0) {
    return {
      records: [],
      incomplete: true,
      missing_pairs: [],
      reason: "No eligible Persona is available for the task matrix; ineligible simulated Personas cannot complete S4b.",
    };
  }

  const expected = eligibleIds.flatMap((personaId) => taskKeys.map((taskKey) => `${personaId}::${taskKey}`));
  const buckets = new Map(expected.map((key) => [key, []]));
  for (const record of records ?? []) {
    const key = `${record?.persona_id}::${record?.task_key}`;
    if (buckets.has(key)) buckets.get(key).push(record);
  }

  const missingPairs = [];
  const duplicatePairs = [];
  const normalized = expected.map((key) => {
    const [personaId, taskKey] = key.split("::");
    const matches = buckets.get(key);
    if (matches.length === 1) return matches[0];
    if (matches.length > 1) duplicatePairs.push(key);
    if (matches.length === 0) missingPairs.push(key);
    return {
      persona_id: personaId,
      task_key: taskKey,
      result: "not_executed",
      reason: matches.length > 1 ? "duplicate executor records; result withheld" : "executor returned no record for this required pair",
      path: [],
      hesitation_steps: [],
      errors: [],
      abandon_reason: null,
      cognitive_walkthrough: null,
      cause_type: "unknown",
      evidence_refs: [],
    };
  });
  const incomplete = missingPairs.length > 0 || duplicatePairs.length > 0;
  return {
    records: normalized,
    incomplete,
    missing_pairs: [...missingPairs, ...duplicatePairs],
    reason: incomplete
      ? `Task matrix integrity failed: ${missingPairs.length} missing pair(s), ${duplicatePairs.length} duplicate pair(s).`
      : "Task matrix complete.",
  };
}

function normalizeSwitchingScenarios(scenarios, personas) {
  const personaById = new Map((personas ?? []).map((persona) => [persona.persona_id, persona]));
  return (scenarios ?? []).map((scenario) => {
    const persona = personaById.get(scenario.persona_id);
    const maxWorkaroundCost = Math.max(0, ...(persona?.pains ?? []).map((pain) => pain.workaround_cost ?? 0));
    const resolved = resolveSwitchingForces(scenario.switching_forces, maxWorkaroundCost);
    return {
      ...scenario,
      switching_forces: {
        push: resolved.push,
        pull: resolved.pull,
        anxiety: resolved.anxiety,
        habit: resolved.habit,
        verdict: resolved.verdict,
        basis: scenario.switching_forces?.basis ?? "Program recomputed from push, pull, anxiety and habit.",
        push_forced_by_workaround_cost: resolved.push_forced_by_workaround_cost,
      },
      flags: {
        ...(scenario.flags ?? {}),
        pseudo_demand_risk: (scenario.alternatives ?? []).length === 0,
        high_switching_friction: resolved.high_friction,
      },
    };
  });
}

function buildRisks(flags, skipReasons, vetted) {
  const risks = [];
  if (flags.pseudo_demand_risk) risks.push("No current alternative was identified: pseudo-demand risk (KB-USR-S2).");
  if (flags.high_switching_friction) risks.push("Switching friction exceeds drive: users may not switch even if the product is better.");
  if (flags.value_communication_failure) risks.push("Value communication failure: fix positioning and copy before features.");
  if (flags.retention_risk) risks.push("Journey breaks between first use and continued use: retention risk.");
  if (flags.politeness_only_feedback) risks.push("Feedback was polite but behaviourally empty; treated as weight 0.");
  for (const skip of skipReasons) risks.push(`${skip.unit} not executed: ${skip.reason}`);
  for (const rejection of vetted.rejected) risks.push(`Validation plan ${rejection.plan_id} rejected: ${rejection.problems[0]}`);
  return risks;
}

function buildMissingInformation(input, availability, breadth) {
  const missing = [];
  if (!Array.isArray(input.product_tasks) || input.product_tasks.length === 0) {
    missing.push({
      field: "product_tasks",
      state: "missing",
      why_it_matters:
        "Without a core task script, task testing cannot run and no task result may be reported. Fabricating one would be the worst possible failure here.",
      affected_units: ["s4b"],
      affected_dimensions: ["pain_severity"],
      how_to_obtain: "Supply 3-5 core tasks with expected observable outcomes, using the same script as the technical audit.",
    });
  }
  if (!Array.isArray(input.existing_user_evidence) || input.existing_user_evidence.length === 0) {
    missing.push({
      field: "existing_user_evidence",
      state: "missing",
      why_it_matters:
        "With no E3+ real-user evidence, every conclusion is simulation-only (E2) and user value is capped at medium.",
      affected_units: ["s2", "s5", "s6"],
      affected_dimensions: ["demand_strength", "usage_frequency", "willingness_to_pay"],
      how_to_obtain: "Supply interview notes, usage data, or payment records with tier, source and timestamp.",
    });
  }
  if (!input.product_profile?.url && !input.product_profile?.experience_report_ref) {
    missing.push({
      field: "product_profile.url",
      state: "missing",
      why_it_matters: "No product surface and no upstream experience report: first-experience simulation cannot run.",
      affected_units: ["s4a", "s4b"],
      affected_dimensions: [],
      how_to_obtain: "Supply a reachable product url or an upstream experience_report_ref.",
    });
  }
  if (!input.constraints) {
    missing.push({
      field: "constraints",
      state: "unknown",
      why_it_matters: "Without time and budget limits, plan feasibility cannot be checked and is reported as unchecked.",
      affected_units: ["s6"],
      affected_dimensions: [],
      how_to_obtain: "Supply time_budget_weeks, money_budget_cny and recruitable_channels.",
    });
  }
  if (breadth.verdict === "borderline") {
    missing.push({
      field: "target_users.raw_description",
      state: "unknown",
      why_it_matters: "The target user description lacks quantified qualifiers, so persona confidence is reduced.",
      affected_units: ["s1", "s2"],
      affected_dimensions: [],
      how_to_obtain: "Add behavioural qualifiers: frequency, budget, deadline, current alternative.",
    });
  }
  void availability;
  return missing;
}

function buildSummary({ valueJudgment, confidenceLabel, status, skipReasons, executedAny, plans }) {
  void confidenceLabel;
  void status;
  void plans;
  if (!executedAny && skipReasons.length > 0) {
    return "用户需求暂不能确认：当前缺少能够完成用户侧检查的关键输入或能力，请先补齐最影响判断的信息。";
  }
  const messages = {
    strong: "用户需求较强：已有用户证据支持核心需求，下一步重点是把关键体验做稳并继续观察复用与付费。",
    medium: "用户需求中等：方向已经出现信号，但持续使用、替代优势或付费价值仍有部分需要继续验证。",
    weak: "用户需求偏弱：当前持续使用动力或产品差异还不够强，优先解决最影响复用的问题后再复测。",
    very_weak: "用户需求偏弱：当前持续使用动力或产品差异还不够强，优先解决最影响复用的问题后再复测。",
    unverified: "用户需求未验证：目前没有足够真实用户证据支撑强弱判断，先补最关键的真实行为数据。",
  };
  return messages[valueJudgment.judgment] ?? "用户需求未验证：先补最关键的真实用户证据。";
}

function zhJudgment(judgment) {
  return { strong: "强", medium: "中", weak: "弱", very_weak: "极弱", unverified: "未验证" }[judgment] ?? judgment;
}

export { blockedResult, buildUserSpecialistReportV2, selectUserSpecialistReportV2 };
