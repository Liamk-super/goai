/**
 * Handoff assembly. Implements SKILL_SPEC_V0.1 section 5 (four-way handoff).
 *
 * Each downstream agent receives a FIELD SLICE, not the whole report. The
 * boundary is the point: this skill hands over user-side facts and evidence
 * strength, and never the conclusions that belong to another agent.
 *
 * Absent from every slice by design: any project-level decision
 * (proceed / keep validating / adjust / pause). That is the supervisor's call.
 *
 * Field names here are fixed by schema/output.schema.json (handoff is a closed
 * object). Adding a field requires changing the contract first.
 */

/** Experience problems and cognitive attribution -> product & team expert. */
function toProductTeamExpert(output) {
  const journeyDropOffs = (output.scenarios_and_alternatives ?? []).flatMap((scenario) =>
    (scenario.journey ?? [])
      .filter((stage) => stage.drop_off_risk === "high")
      .map((stage) => ({
        scenario_id: scenario.scenario_id,
        persona_id: scenario.persona_id,
        stage: stage.stage,
        pain: stage.pain,
        emotion: stage.emotion,
      })),
  );

  return {
    experience_issues: output.simulated_findings?.experience_issues ?? [],
    journey_drop_offs: journeyDropOffs,
    value_communication_failure: output.simulated_findings?.value_communication_failure ?? false,
    task_test_matrix: output.simulated_findings?.task_test_matrix ?? [],
    contract_note:
      "User-side phenomena and cognitive attribution only; all of it is E2 simulation. " +
      "Functional and technical attribution is your call. Where our cause_type disagrees with yours, " +
      "both are retained as a conflict rather than reconciled here.",
  };
}

/** Demand-side facts and evidence strength -> investment & business. */
function toInvestmentBusiness(output) {
  const dimensions = output.user_value_score?.dimensions ?? {};
  const pick = (key) => ({
    score: dimensions[key]?.score ?? null,
    counted: dimensions[key]?.counted ?? false,
    cap_reason: dimensions[key]?.cap_reason ?? null,
    max_tier: dimensions[key]?.max_tier ?? "E0",
    evidence_refs: dimensions[key]?.evidence_refs ?? [],
  });

  const segments = output.target_user_definition?.converged_segments ?? [];

  return {
    demand_strength: pick("demand_strength"),
    usage_frequency: pick("usage_frequency"),
    willingness_to_pay: pick("willingness_to_pay"),
    virality_potential: pick("virality"),
    payer_vs_user: {
      segments: segments.map((segment) => ({
        segment_id: segment.segment_id,
        who_pays: segment.who_pays,
        who_adopts_first: segment.who_adopts_first,
        payer_differs_from_user: segment.payer_differs_from_user ?? null,
        fact_type: segment.fact_type,
      })),
    },
    alternatives: (output.scenarios_and_alternatives ?? []).flatMap((scenario) =>
      (scenario.alternatives ?? []).map((alternative) => ({
        scenario_id: scenario.scenario_id,
        name: alternative.name,
        alternative_type: alternative.alternative_type,
        cost: alternative.cost,
        gap: alternative.gap,
        fact_type: alternative.fact_type,
      })),
    ),
    switching_forces_summary: (output.scenarios_and_alternatives ?? []).map((scenario) => ({
      scenario_id: scenario.scenario_id,
      persona_id: scenario.persona_id,
      ...(scenario.switching_forces ?? {}),
    })),
    contract_note:
      "Demand-side evidence strength only. Market sizing, pricing and investment value are yours. " +
      "willingness_to_pay is an ordinal signal, not a price point; stated intent is recorded as a weak signal. " +
      `Real-user evidence present: ${output.evidence_level_summary?.has_real_user_evidence ?? false}.`,
  };
}

/** Full evidence set and conflicts -> evidence calibration. */
function toEvidenceCalibration(output, ingestedEvidence = []) {
  const issued = output.evidence_cards ?? [];
  return {
    evidence_cards: issued,
    issued_evidence_cards: issued,
    ingested_evidence_refs: ingestedEvidence.map((record) => record.evidence_id),
    ingested_evidence: ingestedEvidence,
    fact_inference_assumption_split: {
      fact: countFactType(output, "fact"),
      inference: countFactType(output, "inference"),
      assumption: countFactType(output, "assumption"),
    },
    conflict_pairs: (output.conflicts ?? []).map((conflict) => ({
      conflict_id: conflict.conflict_id,
      conflict_type: conflict.conflict_type,
      side_a_ref: conflict.side_a?.ref ?? null,
      side_b_ref: conflict.side_b?.ref ?? null,
      resolution: conflict.resolution,
      winner_ref: conflict.winner_ref ?? null,
      demoted_ref: conflict.demoted_ref ?? null,
    })),
    downgraded_entries: output.evidence_level_summary?.downgraded_entries ?? [],
    expiry_unknown_refs: [...issued, ...ingestedEvidence]
      .filter((entry) => entry.expiry === "unknown")
      .map((entry) => entry.evidence_id),
    simulation_capped: true,
    contract_note:
      "All simulated cards are capped at E2 by program, never by prompt. This skill issues no E3+ card. " +
      "Expiry is not defaulted here (DECISIONS D-04); entries in expiry_unknown_refs need your validity ruling. " +
      "On rejection we re-evidence or downgrade; we do not argue.",
  };
}

function countFactType(output, wanted) {
  let count = 0;
  const visit = (value) => {
    if (Array.isArray(value)) return value.forEach(visit);
    if (!value || typeof value !== "object") return;
    if (value.fact_type === wanted) count += 1;
    Object.values(value).forEach(visit);
  };
  visit(output);
  return count;
}

/** Judgment, critical issue, plans -> review supervisor. */
function toReviewSupervisor(output, { ingestedEvidence = [], judgmentEvidenceRefs = [] } = {}) {
  // Shared contract shape: {action, owner_hint, verifiable_outcome}, max 3.
  // owner_hint is always human — this skill designs validation, never runs it.
  const functionalFailure = (output.simulated_findings?.task_test_matrix ?? []).find(
    (task) => task.result === "failed" && task.cause_type === "functional",
  );
  const planActions = (output.validation_plans ?? []).filter((plan) => plan.duration?.fits_constraints !== false).slice(0, 3).map((plan) => ({
    action:
      `${plan.plan_id}: run ${plan.method} for ${plan.hypothesis_id} ` +
      `(${plan.duration?.weeks ?? "?"}w, approval required before any real-user contact)`,
    owner_hint: "human",
    verifiable_outcome:
      `${plan.hypothesis_id} moves ${plan.current_evidence_level} -> ${plan.target_evidence_level} ` +
      `when ${plan.success_threshold?.expression ?? "the stated threshold"} is met`,
  }));
  const nextActions = functionalFailure
    ? [{
        action: `Handoff to Product Team Expert to fix ${functionalFailure.task_key}, then retest the same task for ${functionalFailure.persona_id}.`,
        owner_hint: "product_team_expert_agent",
        verifiable_outcome: `${functionalFailure.task_key} completes without the recorded functional blocker for ${functionalFailure.persona_id}.`,
      }, ...planActions].slice(0, 3)
    : planActions;

  return {
    overall_judgment: output.overall_judgment ?? null,
    user_value_judgment: output.user_value_judgment ?? null,
    evidence_confidence: output.evidence_confidence ?? null,
    critical_issue: output.critical_issue ?? null,
    key_real_evidence_refs: judgmentEvidenceRefs.filter((ref) =>
      ingestedEvidence.some((record) => record.evidence_id === ref),
    ),
    next_actions: nextActions,
    top_risks: (output.top_user_problems ?? []).slice(0, 3).map((problem) => ({
      problem_id: problem.problem_id,
      question: problem.question,
      blocks_which_judgment: problem.blocks_which_judgment,
    })),
    validation_plans_digest: (output.validation_plans ?? []).map((plan) => ({
      plan_id: plan.plan_id,
      hypothesis_id: plan.hypothesis_id,
      method: plan.method,
      current_evidence_level: plan.current_evidence_level,
      target_evidence_level: plan.target_evidence_level,
      duration_weeks: plan.duration?.weeks ?? null,
      needs_human_review: plan.needs_human_review,
    })),
    missing_information: output.missing_information ?? [],
    needs_human_review: (output.validation_plans ?? []).some((plan) => plan.needs_human_review === true),
    contract_note:
      "User-side judgment only; no project-level recommendation is included. " +
      "user_value_judgment is the internal five-band verdict; overall_judgment is the shared four-value field. " +
      "The score is an ordinal for demand-side evidence strength, not a success probability and not a measure of liking.",
  };
}

export function buildHandoff(output, context = {}) {
  const handoff = {
    to_product_team_expert_agent: toProductTeamExpert(output),
    to_investment_business_agent: toInvestmentBusiness(output),
    to_evidence_calibration_agent: toEvidenceCalibration(output, context.ingestedEvidence ?? []),
    to_review_supervisor_agent: toReviewSupervisor(output, context),
  };
  if (context.failureSafe === true) {
    handoff.to_review_supervisor_agent = {
      ...handoff.to_review_supervisor_agent,
      overall_judgment: "insufficient_evidence",
      user_value_judgment: "unverified",
      evidence_confidence: "low",
      validation_plans_digest: [],
      next_actions: [{
        action: "Resolve the failed or blocked validation step, then rerun before adopting any user-value verdict.",
        owner_hint: "review_supervisor_agent",
        verifiable_outcome: "A subsequent run completes the failed dependency with a contract-valid result.",
      }],
      needs_human_review: true,
    };
  }
  return handoff;
}

export function emptyHandoff() {
  return buildHandoff({});
}
