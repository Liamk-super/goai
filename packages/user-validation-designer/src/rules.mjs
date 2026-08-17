/**
 * Deterministic rule engine for user-validation-designer.
 *
 * Everything here is computed by PROGRAM, never by the model
 * (SKILL_SPEC_V0.1 §6.A). The model supplies raw material — persona content,
 * force ratings, interview text — and this module decides what it means.
 *
 * Every constant traces to the user-side knowledge base. The three exceptions
 * are marked NEW-DECISION and are pending human sign-off; they are named in
 * SKILL.md §15 so they cannot hide.
 */

/** KB-USR-VS01 weights, total 100. Note the 10 — see DECISIONS D-03. */
export const WEIGHTS = Object.freeze({
  demand_strength: 20,
  usage_frequency: 20,
  pain_severity: 20,
  alternative_gap: 15,
  willingness_to_pay: 15,
  virality: 10,
});

export const DIMENSION_KEYS = Object.freeze(Object.keys(WEIGHTS));

export const TIER_ORDER = Object.freeze(["E0", "E1", "E2", "E3", "E4", "E5"]);

/** Simulation ceiling. KB global convention: nothing this skill simulates exceeds E2. */
export const SIMULATION_CEILING = "E2";

export function tierOrdinal(tier) {
  const index = TIER_ORDER.indexOf(tier);
  return index === -1 ? 0 : index;
}

export function isRealUserTier(tier) {
  return tierOrdinal(tier) >= tierOrdinal("E3");
}

/** A-01: clamp any tier to the simulation ceiling. Returns the clamped tier. */
export function clampSimulatedTier(tier) {
  return tierOrdinal(tier) > tierOrdinal(SIMULATION_CEILING) ? SIMULATION_CEILING : tier;
}

// --- Dimension scaffolding --------------------------------------------------

export function emptyDimensions() {
  const dimensions = {};
  for (const key of DIMENSION_KEYS) {
    dimensions[key] = {
      score: null,
      weight: WEIGHTS[key],
      counted: false,
      cap_reason: "no_user_evidence",
      evidence_refs: [],
      basis: "unassessed",
      max_tier: "E0",
      needs_rescore: false,
      real_evidence_overrides_simulation: false,
    };
  }
  return dimensions;
}

export function emptyFlags() {
  return {
    target_user_too_broad: false,
    persona_homogeneous: false,
    simulation_unrealistic: false,
    pseudo_demand_risk: false,
    value_communication_failure: false,
    high_switching_friction: false,
    retention_risk: false,
    politeness_only_feedback: false,
    compliance_concern: false,
    external_action_pending_approval: false,
    prompt_injection_observed: false,
    conflict: false,
  };
}

/**
 * A-17: a dimension counts only when backed by evidence above team self-report.
 * KB-USR-VS01 校准规则: "如果某维度只有团队自述支撑(<=E1)，那么该维度标'待验证'不计分".
 *
 * Reads the tier of each referenced evidence card. No refs => not counted:
 * an unevidenced score is an opinion, and opinions do not enter a 100-point scale.
 */
export function applyCounting(dimensions, evidenceCards, { eligiblePersonaIds = null } = {}) {
  const byId = new Map(evidenceCards.map((card) => [card.evidence_id, card]));
  const eligible = eligiblePersonaIds === null ? null : new Set(eligiblePersonaIds);
  const result = structuredClone(dimensions);

  for (const key of DIMENSION_KEYS) {
    const dimension = result[key];
    const refs = dimension.evidence_refs ?? [];
    const usable = refs
      .map((ref) => byId.get(ref))
      .filter(Boolean)
      .filter((entry) => {
        if (entry.integrity_valid === false || entry.scope_valid === false) return false;
        const declaredDimensions = entry.applicability?.valid_for_dimensions;
        if (Array.isArray(declaredDimensions) && !declaredDimensions.includes(key)) return false;
        if (eligible === null || entry.origin === "caller_supplied") return true;
        const personaIds = entry.applicability?.persona_ids;
        if (!Array.isArray(personaIds) || personaIds.length === 0) return false;
        if (isPolitenessOnlyObservation(entry.observation)) return false;
        return personaIds.some((personaId) => eligible.has(personaId));
      });
    const tiers = usable.map((entry) => entry.reliability_level).filter(Boolean);
    dimension.evidence_refs = refs.filter((ref) => usable.some((entry) => entry.evidence_id === ref));
    const best = tiers.reduce((acc, tier) => (tierOrdinal(tier) > tierOrdinal(acc) ? tier : acc), "E0");
    dimension.max_tier = best;

    if (typeof dimension.score !== "number" || tiers.length === 0) {
      dimension.counted = false;
      dimension.cap_reason = tiers.length === 0 ? "no_user_evidence" : (dimension.cap_reason ?? "no_user_evidence");
      dimension.score = null;
      continue;
    }

    if (tierOrdinal(best) <= tierOrdinal("E1")) {
      dimension.counted = false;
      dimension.cap_reason = "team_self_report_only";
      dimension.score = null;
      continue;
    }

    dimension.counted = true;
    dimension.cap_reason = null;
  }
  return result;
}

export function countedKeys(dimensions) {
  return DIMENSION_KEYS.filter((key) => dimensions[key]?.counted === true);
}

export function uncountedCount(dimensions) {
  return DIMENSION_KEYS.length - countedKeys(dimensions).length;
}

/**
 * KB-USR-VS01 formula + NEW-DECISION-U02 (DECISIONS D-01).
 *
 * `raw_total`        raw sum over counted dimensions only (max = counted_weight)
 * `normalized_total` rescaled to a 100-point basis so KB-USR-VS03's 80/65/50
 *                    thresholds keep their meaning
 *
 * Both are emitted. VS03 is applied to normalized_total; raw_total is retained
 * so a human can re-derive the judgment under a different rule if D-01 changes.
 */
export function scoreTotals(dimensions) {
  const counted = countedKeys(dimensions);
  if (counted.length === 0) {
    return { raw_total: null, normalized_total: null, counted_weight: 0 };
  }
  let sum = 0;
  let weight = 0;
  for (const key of counted) {
    sum += (dimensions[key].score / 5) * WEIGHTS[key];
    weight += WEIGHTS[key];
  }
  const round = (n) => Math.round(n * 100) / 100;
  return {
    raw_total: round(sum),
    normalized_total: round((sum / weight) * 100),
    counted_weight: weight,
  };
}

/**
 * KB-USR-VS03 判断映射 + KB-USR-VS01 封顶规则.
 * Frozen by DECISIONS D-01; do not alter these bands.
 *
 * Order matters: the unverified gate and the E3+ ceiling both outrank the score.
 */
export function userValueJudgment({ normalized_total, dimensions, hasRealUserEvidence }) {
  // >=3 dimensions uncounted -> no strength claim at all (VS03).
  if (uncountedCount(dimensions) >= 3) {
    return { judgment: "unverified", preliminary: true, ceiling_applied: false, reason: ">=3 dimensions uncounted for lack of evidence" };
  }
  if (normalized_total === null) {
    return { judgment: "unverified", preliminary: true, ceiling_applied: false, reason: "no dimension could be scored" };
  }

  const demand = dimensions.demand_strength?.score;
  let judgment;
  if (normalized_total >= 80 && typeof demand === "number" && demand >= 4) judgment = "strong";
  else if (normalized_total >= 80) judgment = "medium"; // >=80 without demand>=4 fails the strong gate
  else if (normalized_total >= 65) judgment = "medium";
  else if (normalized_total >= 50) judgment = "weak";
  else judgment = "very_weak";

  // KB-USR-VS01 封顶: no E3+ evidence => at most medium, marked preliminary/E2.
  if (!hasRealUserEvidence && (judgment === "strong")) {
    return {
      judgment: "medium",
      preliminary: true,
      ceiling_applied: true,
      reason: "no E3+ real-user evidence: user value capped at medium (KB-USR-VS01)",
    };
  }
  return {
    judgment,
    preliminary: !hasRealUserEvidence,
    ceiling_applied: false,
    reason: hasRealUserEvidence ? "scored with real-user evidence present" : "simulation-only, marked preliminary/E2",
  };
}

/**
 * DECISIONS D-02: map the internal five-band judgment onto the PUBLIC enum
 * shared with product-technical-audit. The public enum is
 * ["strong","medium","weak","insufficient_evidence"] and is not extended here.
 *
 * very_weak -> weak            (assessed and poor)
 * unverified -> insufficient_evidence  (could not assess — a different claim)
 */
export function toOverallJudgment(userValueJudgmentValue) {
  switch (userValueJudgmentValue) {
    case "strong":
      return "strong";
    case "medium":
      return "medium";
    case "weak":
    case "very_weak":
      return "weak";
    case "unverified":
    default:
      return "insufficient_evidence";
  }
}

export function evidenceConfidence({ dimensions, hasRealUserEvidence, flags, status, allRealEvidenceUnderpowered = false, targetBorderline = false }) {
  const uncounted = uncountedCount(dimensions);
  if (uncounted >= 3 || status === "blocked") return "low";
  if (!hasRealUserEvidence) return flags.conflict === true || status === "partial" ? "low" : "medium";
  if (uncounted >= 1 || flags.conflict === true || status === "partial" || allRealEvidenceUnderpowered || targetBorderline) return "medium";
  return "high";
}

/** Vague praise without behaviour, cost, time or a concrete outcome has zero scoring weight. */
export function isPolitenessOnlyObservation(text) {
  const value = String(text ?? "").normalize("NFC").trim();
  if (!/(挺好|很好|不错|满意|喜欢|还可以|looks? good|nice|great|love it)/iu.test(value)) return false;
  return !/(\d|完成|失败|放弃|支付|付费|点击|留资|分钟|小时|天|次|元|成本|行为|task|paid|payment|click|complete|failed|minute|hour)/iu.test(value);
}

// --- KB-USR-F02 switching forces -------------------------------------------

/**
 * KB-USR-F02 判断规则:
 *   push+pull <= anxiety+habit            -> will_not_switch
 *   workaround_cost >= 4                  -> push forced to >= 4
 */
export function resolveSwitchingForces(forces, maxWorkaroundCost = 0) {
  const clamp = (n) => (typeof n === "number" ? Math.min(5, Math.max(1, Math.round(n))) : 1);
  const push = maxWorkaroundCost >= 4 ? Math.max(4, clamp(forces?.push)) : clamp(forces?.push);
  const pull = clamp(forces?.pull);
  const anxiety = clamp(forces?.anxiety);
  const habit = clamp(forces?.habit);

  const forceAdjusted = maxWorkaroundCost >= 4 && clamp(forces?.push) < 4;
  const drive = push + pull;
  const friction = anxiety + habit;

  return {
    push,
    pull,
    anxiety,
    habit,
    verdict: drive <= friction ? "will_not_switch" : "will_switch",
    high_friction: friction >= drive,
    push_forced_by_workaround_cost: forceAdjusted,
  };
}

// --- NEW-DECISION-U01: persona homogeneity ---------------------------------

export const BEHAVIOR_KEYS = Object.freeze([
  "alternative_in_use",
  "budget_constraint",
  "skill_level",
  "urgency",
  "risk_attitude",
]);

/** Threshold for "same persona wearing two names". NEW-DECISION-U01. */
export const HOMOGENEITY_THRESHOLD = 4;

function normalizeKeyValue(value) {
  return typeof value === "string" ? value.normalize("NFC").trim().replace(/\s+/gu, " ").toLowerCase() : value;
}

/**
 * A-07/A-08: structural persona-set check.
 *
 * KB-USR-G02 says a set whose members reach the same conclusion has failed
 * differentiation, which is not directly computable. This implements the
 * frozen proxy: >=HOMOGENEITY_THRESHOLD of the five behaviour keys identical.
 */
export function checkPersonaSet(personas) {
  const list = Array.isArray(personas) ? personas : [];
  const homogeneous_pairs = [];

  for (let i = 0; i < list.length; i += 1) {
    for (let j = i + 1; j < list.length; j += 1) {
      let same = 0;
      for (const key of BEHAVIOR_KEYS) {
        const a = normalizeKeyValue(list[i]?.behavior_keys?.[key]);
        const b = normalizeKeyValue(list[j]?.behavior_keys?.[key]);
        if (a !== undefined && a !== null && a === b) same += 1;
      }
      if (same >= HOMOGENEITY_THRESHOLD) {
        const shared = BEHAVIOR_KEYS.filter(
          (key) =>
            normalizeKeyValue(list[i]?.behavior_keys?.[key]) === normalizeKeyValue(list[j]?.behavior_keys?.[key]),
        );
        homogeneous_pairs.push({
          persona_a: list[i]?.persona_id ?? `#${i}`,
          persona_b: list[j]?.persona_id ?? `#${j}`,
          shared_keys: shared,
        });
      }
    }
  }

  const archetypes = new Set(list.map((persona) => persona?.archetype));
  const archetype_coverage = {
    high_need: archetypes.has("high_need"),
    skeptic: archetypes.has("skeptic"),
    edge_case: archetypes.has("edge_case"),
  };

  let pairwise_min_distinct_keys = BEHAVIOR_KEYS.length;
  if (list.length >= 2) {
    for (let i = 0; i < list.length; i += 1) {
      for (let j = i + 1; j < list.length; j += 1) {
        let distinct = 0;
        for (const key of BEHAVIOR_KEYS) {
          const a = normalizeKeyValue(list[i]?.behavior_keys?.[key]);
          const b = normalizeKeyValue(list[j]?.behavior_keys?.[key]);
          if (a !== b) distinct += 1;
        }
        pairwise_min_distinct_keys = Math.min(pairwise_min_distinct_keys, distinct);
      }
    }
  }

  const countOk = list.length >= 3 && list.length <= 5;
  const coverageOk = Object.values(archetype_coverage).every(Boolean);
  const verdict = countOk && coverageOk && homogeneous_pairs.length === 0 ? "pass" : "fail";

  return {
    count: list.length,
    archetype_coverage,
    differentiation: { pairwise_min_distinct_keys, homogeneous_pairs, verdict },
    retries_used: 0,
  };
}

/**
 * Human-readable reasons a persona set failed. Derived from the check rather
 * than carried inside it, because the output contract for persona_set_check is
 * closed (additionalProperties: false).
 */
export function personaSetFailureReasons(check) {
  const reasons = [];
  const count = check?.count ?? 0;
  if (count < 3 || count > 5) reasons.push(`persona count ${count} outside 3-5 (KB-USR-G02)`);
  const coverage = check?.archetype_coverage ?? {};
  const missing = Object.entries(coverage).filter(([, present]) => !present).map(([name]) => name);
  if (missing.length > 0) reasons.push(`missing required archetype(s): ${missing.join(", ")}`);
  const pairs = check?.differentiation?.homogeneous_pairs ?? [];
  if (pairs.length > 0) {
    reasons.push(`homogeneous pair(s): ${pairs.map((pair) => `${pair.persona_a}~${pair.persona_b}`).join(", ")}`);
  }
  return reasons;
}

/** KB-USR-G01: six elements + explicit thresholds, else the persona cannot score. */
export function personaEligibility(persona) {
  const missing = [];
  if (!persona?.background) missing.push("background");
  if (!persona?.goal_statement) missing.push("goal_statement");
  if (!persona?.motivation) missing.push("motivation");
  if (!Array.isArray(persona?.pains) || persona.pains.length === 0) missing.push("pains");
  if (!Array.isArray(persona?.barriers) || persona.barriers.length === 0) missing.push("barriers");
  if (!persona?.value_threshold?.statement) missing.push("value_threshold");
  if (!persona?.rejection_threshold?.statement) missing.push("rejection_threshold");
  if (!Array.isArray(persona?.rejection_reasons) || persona.rejection_reasons.length === 0) {
    missing.push("rejection_reasons");
  }
  if (missing.length > 0) return { eligible: false, missing, reason: "required_field_missing" };
  if (persona?.confidence === "low") return { eligible: false, missing: [], reason: "low_confidence" };
  return { eligible: true, missing: [], reason: "eligible" };
}

// --- A-10: simulation realism ---------------------------------------------

/**
 * KB-USR-B02 / S5 分支: a simulation round with no negative finding and no
 * hidden need is not a good result, it is a broken simulation. Real users
 * always have complaints.
 */
export function checkRealism({
  negativeFindings = 0,
  hiddenNeeds = 0,
  retriesUsed = 0,
  executed = true,
  interviews = null,
  personaIds = null,
}) {
  // No simulation ran at all: that is "not applicable", not "unrealistic".
  // Reporting fail here would blame the model for a step that never executed.
  if (!executed) {
    return {
      negative_findings_count: 0,
      hidden_needs_count: 0,
      verdict: "not_applicable",
      retries_used: retriesUsed,
    };
  }
  const interviewList = Array.isArray(interviews) ? interviews : [];
  const expected = Array.isArray(personaIds) ? personaIds : [];
  const completePerPersona =
    expected.length === 0 ||
    expected.every((personaId) => {
      const matches = interviewList.filter((interview) => interview?.persona_id === personaId);
      return (
        matches.length === 1 &&
        Array.isArray(matches[0].questions_raised) &&
        matches[0].questions_raised.length > 0 &&
        Array.isArray(matches[0].complaints) &&
        matches[0].complaints.length > 0
      );
    });
  return {
    negative_findings_count: negativeFindings,
    hidden_needs_count: hiddenNeeds,
    verdict: negativeFindings > 0 && hiddenNeeds > 0 && completePerPersona ? "pass" : "fail",
    retries_used: retriesUsed,
  };
}

/** Why a realism check failed. Kept outside the closed contract object. */
export function realismFailureReason(check) {
  if (check?.verdict === "pass") return "simulation produced complaints and hidden needs";
  if (check?.verdict === "not_applicable") return "no simulation ran; realism is not applicable";
  return "zero negative findings, zero hidden needs, or incomplete per-persona interviews: simulation is unrealistic (KB-USR-B02)";
}

export const MAX_SIMULATION_RETRIES = 2;

// --- NEW-DECISION-U03: hypothesis priority --------------------------------

const DECISION_IMPACT_WEIGHT = Object.freeze({ blocking: 40, high: 30, medium: 20, low: 10 });

/**
 * priority_score = impact_weight * (5 - tier_ordinal) * sum(affected dimension weights) / 100
 *
 * Rationale: what blocks a decision, is weakly evidenced, and touches heavy
 * dimensions gets validated first. NEW-DECISION-U03, pending sign-off.
 */
export function hypothesisPriority(hypothesis) {
  const impact = DECISION_IMPACT_WEIGHT[hypothesis?.decision_impact] ?? 10;
  const tierGap = Math.max(0, 5 - tierOrdinal(hypothesis?.current_evidence_level ?? "E0"));
  const dimensionWeight = (hypothesis?.affected_dimensions ?? []).reduce(
    (sum, key) => sum + (WEIGHTS[key] ?? 0),
    0,
  );
  const score = (impact * tierGap * Math.max(dimensionWeight, 1)) / 100;
  return Math.round(score * 100) / 100;
}

export function rankHypotheses(hypotheses) {
  const scored = (hypotheses ?? []).map((hypothesis) => ({
    ...hypothesis,
    priority_score: hypothesisPriority(hypothesis),
  }));
  scored.sort((a, b) => {
    if (b.priority_score !== a.priority_score) return b.priority_score - a.priority_score;
    return String(a.hypothesis_id).localeCompare(String(b.hypothesis_id));
  });
  return scored.map((hypothesis, index) => ({ ...hypothesis, priority_rank: index + 1 }));
}

// --- A-06: conflict resolution --------------------------------------------

/**
 * KB-USR-B04: when simulation contradicts E3+ evidence, real evidence wins and
 * the simulated conclusion is demoted to reference_only. Both sides are
 * RETAINED and never averaged — averaging would manufacture a number that no
 * evidence supports.
 */
export function resolveConflict(sideA, sideB) {
  const aReal = isRealUserTier(sideA?.tier);
  const bReal = isRealUserTier(sideB?.tier);

  if (aReal && !bReal) {
    return { resolution: "real_evidence_wins", winner_ref: sideA.ref, demoted_ref: sideB.ref };
  }
  if (bReal && !aReal) {
    return { resolution: "real_evidence_wins", winner_ref: sideB.ref, demoted_ref: sideA.ref };
  }
  // Two real sources disagreeing is not ours to arbitrate.
  if (aReal && bReal) {
    return { resolution: "unresolved", winner_ref: null, demoted_ref: null };
  }
  return { resolution: "both_retained", winner_ref: null, demoted_ref: null };
}
