/**
 * Validation-plan gate. Implements A-14 / A-15 / A-16 (SKILL_SPEC_V0.1 §6.A)
 * and the KB-USR-V01..V04 rules.
 *
 * The purpose of this module is to make one promise mechanical: a plan that
 * survives here WILL raise the evidence level of a named claim if it is
 * executed. KB-USR-V04: "如果某方案完成后不升级任何结论的证据等级，那么该方案删除".
 */

import { tierOrdinal } from "./rules.mjs";

/** KB-USR-V02 method matrix, plus the tier each method can actually reach (V04). */
export const METHOD_MATRIX = Object.freeze({
  problem_interview: { max_tier: "E3", validates: ["demand", "behavior", "segment", "alternative"] },
  usability_test: { max_tier: "E3", validates: ["usability", "behavior"] },
  survey: { max_tier: "E3", validates: ["demand", "segment"] },
  landing_page_test: { max_tier: "E4", validates: ["demand", "willingness_to_pay", "segment"] },
  trial_cohort_retention: { max_tier: "E4", validates: ["retention", "behavior"] },
  pricing_experiment: { max_tier: "E5", validates: ["willingness_to_pay"] },
  presale_or_deposit: { max_tier: "E5", validates: ["willingness_to_pay"] },
});

/** Methods that inevitably touch real people or the outside world (A-16). */
export const EXTERNAL_METHODS = Object.freeze([
  "problem_interview",
  "usability_test",
  "survey",
  "landing_page_test",
  "trial_cohort_retention",
  "pricing_experiment",
  "presale_or_deposit",
]);

const QUESTION_TYPES = new Set(["last_occurrence", "current_workaround", "past_time_cost", "past_money_cost", "past_failure", "recent_alternative"]);
const E4_COMMITMENTS = new Set(["lead_submitted", "reservation_created", "time_committed"]);
const E5_COMMITMENTS = new Set(["deposit_paid", "payment_completed", "contract_signed"]);

/**
 * KB-USR-V02 判断规则:
 *   - a hypothesis about BEHAVIOUR may not be validated by survey alone
 *   - a hypothesis about PAYMENT must carry a real-commitment behavioural metric
 */
function checkMethodFit(plan, hypothesis) {
  const problems = [];
  const entry = METHOD_MATRIX[plan?.method];
  if (!entry) {
    problems.push(`method "${plan?.method}" is not in the KB-USR-V02 matrix`);
    return problems;
  }

  const claimType = hypothesis?.claim_type;
  if (!hypothesis) problems.push(`hypothesis ${plan?.hypothesis_id ?? "unknown"} does not exist`);
  if (hypothesis && !entry.validates.includes(claimType)) {
    problems.push(`method "${plan.method}" does not validate authoritative claim_type "${claimType}"`);
  }
  if (claimType === "behavior" && plan.method === "survey") {
    problems.push("a behavioural hypothesis cannot be validated by survey alone (KB-USR-V02)");
  }
  if (claimType === "willingness_to_pay") {
    const metrics = plan?.success_metrics ?? [];
    const hasCommitment = metrics.some((metric) => E4_COMMITMENTS.has(metric?.commitment_type) || E5_COMMITMENTS.has(metric?.commitment_type));
    if (!hasCommitment) {
      problems.push(
        "a payment hypothesis needs a real contact/lead/reservation/deposit/payment/contract or time-cost commitment; views and price-button clicks are interest only",
      );
    }
    if (!["landing_page_test", "pricing_experiment", "presale_or_deposit"].includes(plan.method)) {
      problems.push("willingness_to_pay requires a method that observes a real commitment");
    }
  }
  if (claimType === "usability" && plan.method !== "usability_test") problems.push("usability claims require usability_test");
  if (claimType === "retention" && plan.method !== "trial_cohort_retention") problems.push("retention claims require trial_cohort_retention");
  return problems;
}

/**
 * A-14 + V04: every plan must raise a real claim's tier, and the target tier
 * must be reachable by the chosen method.
 */
function checkEvidenceUpgrade(plan, claimTiers, hypothesis) {
  const problems = [];
  // Defensive: a model may hand back a single object instead of the array the
  // contract requires. Report it as a plan problem rather than throwing.
  const raw = plan?.evidence_upgrade;
  if (raw !== undefined && raw !== null && !Array.isArray(raw)) {
    problems.push("evidence_upgrade must be an array of upgrade entries (KB-USR-V04)");
    return problems;
  }
  const upgrades = raw ?? [];
  if (upgrades.length === 0) {
    problems.push("evidence_upgrade is empty: the plan would not raise any evidence level (KB-USR-V04)");
    return problems;
  }

  const methodMax = METHOD_MATRIX[plan?.method]?.max_tier;
  const owned = upgrades.filter((upgrade) => upgrade?.claim_id === plan?.hypothesis_id);
  if (owned.length === 0) problems.push(`plan must upgrade its own hypothesis ${plan?.hypothesis_id}`);
  for (const upgrade of upgrades) {
    if (tierOrdinal(upgrade?.to_tier) <= tierOrdinal(upgrade?.from_tier)) {
      problems.push(
        `evidence_upgrade for ${upgrade?.claim_id} does not advance the tier (${upgrade?.from_tier} -> ${upgrade?.to_tier})`,
      );
    }
    if (methodMax && tierOrdinal(upgrade?.to_tier) > tierOrdinal(methodMax)) {
      problems.push(
        `method "${plan.method}" cannot reach ${upgrade?.to_tier} (max ${methodMax}) for claim ${upgrade?.claim_id}`,
      );
    }
    const text = String(upgrade?.claim ?? "").normalize("NFC").toLowerCase();
    const authoritative = String(hypothesis?.statement ?? "").normalize("NFC").toLowerCase();
    if (upgrade?.claim_id === plan?.hypothesis_id && text && authoritative) {
      const tokens = authoritative.split(/[^\p{L}\p{N}]+/u).filter((token) => token.length >= 2);
      if (tokens.length > 0 && !tokens.some((token) => text.includes(token))) problems.push("owned evidence_upgrade claim text drifts from the authoritative hypothesis");
    }
    // The claim must exist and currently sit at the declared from_tier.
    if (claimTiers && Object.prototype.hasOwnProperty.call(claimTiers, upgrade?.claim_id)) {
      const actual = claimTiers[upgrade.claim_id];
      if (actual !== upgrade.from_tier) {
        problems.push(
          `claim ${upgrade.claim_id} is currently ${actual}, but the plan declares from_tier ${upgrade.from_tier}`,
        );
      }
    } else if (claimTiers) {
      problems.push(`claim ${upgrade?.claim_id} does not exist in evidence_level_summary.per_claim`);
    }
  }

  if (tierOrdinal(plan?.target_evidence_level) <= tierOrdinal(plan?.current_evidence_level)) {
    problems.push(
      `target_evidence_level ${plan?.target_evidence_level} does not advance current ${plan?.current_evidence_level}`,
    );
  }
  const ownedTarget = owned[0]?.to_tier;
  if (ownedTarget && plan?.target_evidence_level !== ownedTarget) {
    problems.push(`target_evidence_level must equal owned upgrade to_tier ${ownedTarget}`);
  }
  if (owned[0] && hypothesis && owned[0].from_tier !== (hypothesis.current_evidence_level ?? "E0")) {
    problems.push(`owned upgrade from_tier must equal authoritative hypothesis tier ${hypothesis.current_evidence_level ?? "E0"}`);
  }

  const commitments = new Set((plan?.success_metrics ?? []).map((metric) => metric?.commitment_type).filter(Boolean));
  if (ownedTarget === "E5" && ![...commitments].some((type) => E5_COMMITMENTS.has(type))) {
    problems.push("E5 requires actual monetary payment, deposit, or contract evidence; lead/reservation/intent is at most E4");
  }
  if (ownedTarget === "E4" && hypothesis?.claim_type === "willingness_to_pay" && ![...commitments].some((type) => E4_COMMITMENTS.has(type) || E5_COMMITMENTS.has(type))) {
    problems.push("E4 willingness-to-pay evidence requires a program-recognized lead, reservation, or time commitment event");
  }
  return problems;
}

/** KB-USR-V03 sample-size floors. Under-powered plans are annotated, not deleted. */
function checkSampleSize(plan) {
  const notes = [];
  const size = plan?.sample_size?.value;
  if (typeof size !== "number") return notes;

  if (plan.method === "problem_interview" && plan.sample_size.unit === "persons_per_persona" && size < 5) {
    notes.push("KB-USR-V03: problem interviews want 5-8 people per persona; below 5 cannot reach saturation");
  }
  if (plan.method === "usability_test" && size < 5) {
    notes.push("KB-USR-V03: a usability round of 5 finds ~85% of common problems; fewer weakens the round");
  }
  if (plan.method === "survey" && size < 100) {
    notes.push("KB-USR-V03: fewer than 100 valid responses is 样本不足 — direction only, no precision");
  }
  return notes;
}

function hasQuantifiedThreshold(plan) {
  const threshold = plan?.success_threshold;
  const metricIds = new Set((plan?.success_metrics ?? []).map((metric) => metric?.metric_id));
  return metricIds.has(threshold?.metric_id) && [">", ">=", "<", "<=", "=", "between"].includes(threshold?.operator) &&
    typeof threshold?.value === "number" && Number.isFinite(threshold.value) && typeof threshold?.unit === "string" && threshold.unit.length > 0;
}

function checkQuestionContent(plan) {
  const problems = [];
  const predictive = /(你会不会|你以后会|你觉得.{0,12}(好吗|好不好|有用|怎么样)|你愿不愿意|会购买吗|would you|will you|do you think|would this be useful)/iu;
  for (const item of plan?.tasks_or_questions ?? []) {
    if (["past_behavior_question", "cost_question"].includes(item?.kind) && !QUESTION_TYPES.has(item?.question_type)) {
      problems.push(`question ${item.item_id ?? "unknown"} has no recognized past-behavior question_type`);
    }
    if (["past_behavior_question", "cost_question"].includes(item?.kind) && predictive.test(String(item?.content ?? ""))) {
      problems.push(`question ${item.item_id ?? "unknown"} is predictive/leading despite its label`);
    }
  }
  for (const [index, question] of (plan?.recruitment_criteria?.screening_questions ?? []).entries()) {
    if (predictive.test(String(question))) problems.push(`screening question ${index + 1} is predictive/leading`);
  }
  return problems;
}

function checkFalsifiability(plan) {
  const statement = String(plan?.validation_target?.falsifiable_statement ?? "");
  const preferenceOnly = /用户.{0,8}(喜欢|觉得不错|认为有帮助|觉得有用)|users? (like|love|find it useful)/iu.test(statement);
  const observable = /(完成|放弃|支付|付款|留资|预约|使用|复购|留存|耗时|成本|比例|转化|点击|任务)/u.test(statement) ||
    /\b(?:complete|abandon|pay|lead|reserve|use|retain|time|cost|rate|conversion|task)\b/iu.test(statement);
  const failureCondition = /(若|如果|低于|高于|少于|不超过|未|不能|则.{0,12}(证伪|否定|失败)|if|below|above|less than|fails?|disprove)/iu.test(statement);
  return preferenceOnly || !observable || !failureCondition ? ["validation_target.falsifiable_statement must name an observable outcome and a decidable failure condition"] : [];
}

function checkParticipantForeignKeys(plan, personaIds) {
  if (!personaIds) return [];
  const ids = plan?.target_participants?.persona_ids ?? [];
  return ids.filter((id) => !personaIds.has(id)).map((id) => `target participant persona ${id} does not exist`);
}

/**
 * A-16: force human review on. Not a suggestion — the schema pins
 * needs_human_review to enum:[true], and this re-asserts it after any model
 * output has been merged, so a model cannot flip it.
 */
function enforceHumanReview(plan) {
  // Derive the forbidden-for-agent actions this plan implies, using the
  // contract's external_actions_required enum. The reasons are surfaced through
  // risks_and_limits so the approver sees *why* without inventing a field that
  // the shared plan contract does not define.
  const actions = new Set(
    Array.isArray(plan?.external_actions_required) ? plan.external_actions_required : [],
  );
  const reasons = [];

  if (EXTERNAL_METHODS.includes(plan?.method)) {
    reasons.push(`method "${plan.method}" involves real users`);
    actions.add("recruit_participants");
    if (plan.method === "survey") actions.add("distribute_survey");
    if (plan.method === "landing_page_test") actions.add("publish_landing_page");
    if (plan.method === "pricing_experiment" || plan.method === "presale_or_deposit") {
      actions.add("publish_landing_page");
      actions.add("charge_or_collect_deposit");
    }
    if (plan.method === "problem_interview" || plan.method === "usability_test") {
      actions.add("contact_users");
    }
  }

  const inclusion = plan?.recruitment_criteria?.inclusion ?? [];
  if (inclusion.length > 0) {
    reasons.push("plan recruits real participants");
    actions.add("recruit_participants");
  }
  if (reasons.length === 0) {
    reasons.push("all real-user validation requires approval before execution");
  }

  const existingRisks = Array.isArray(plan?.risks_and_limits) ? plan.risks_and_limits : [];
  const reviewRisks = reasons.map((r) => `needs_human_review: ${r}`);

  return {
    ...plan,
    execution_owner: "human",
    needs_human_review: true,
    external_actions_required: actions.size > 0 ? [...actions] : null,
    risks_and_limits: [...existingRisks, ...reviewRisks.filter((r) => !existingRisks.includes(r))],
  };
}

/** Feasibility annotation against constraints. Never silently drops a plan. */
function annotateFeasibility(plan, constraints) {
  if (!constraints) {
    return { ...plan, duration: { ...plan.duration, fits_constraints: null, note: "feasibility_unchecked: no constraints supplied" } };
  }
  const weeks = plan?.duration?.weeks;
  const budget = constraints.time_budget_weeks;
  const money = constraints.money_budget_cny;
  const cost = plan?.estimated_cost?.money_cny;
  const personDays = plan?.estimated_cost?.person_days;
  const capacity = constraints.team_capacity_person_days;
  const availableChannels = constraints.recruitable_channels ?? null;
  const requestedChannels = plan?.recruitment_criteria?.channels ?? [];

  const overTime = typeof weeks === "number" && typeof budget === "number" && weeks > budget;
  const overMoney = typeof cost === "number" && typeof money === "number" && cost > money;
  const overCapacity = typeof personDays === "number" && typeof capacity === "number" && personDays > capacity;
  const channelUnavailable = Array.isArray(availableChannels) && availableChannels.length > 0 && !requestedChannels.some((requested) =>
    availableChannels.some((available) => {
      const a = String(available).normalize("NFC").toLowerCase();
      const b = String(requested).normalize("NFC").toLowerCase();
      return a === b || a.includes(b) || b.includes(a);
    }),
  );

  const notes = [];
  if (overTime) notes.push(`needs ${weeks}w but only ${budget}w available`);
  if (overMoney) notes.push(`needs ${cost} CNY but only ${money} CNY available`);
  if (overCapacity) notes.push(`needs ${personDays} person-days but only ${capacity} available`);
  if (channelUnavailable) notes.push("no requested recruitment channel is available under constraints.recruitable_channels");

  return {
    ...plan,
    duration: {
      ...plan.duration,
      fits_constraints: !(overTime || overMoney || overCapacity || channelUnavailable),
      note: notes.length > 0 ? notes.join("; ") : "within stated constraints",
    },
  };
}

/**
 * Vet a set of candidate plans.
 *
 * @param {Array<object>} candidates model-proposed plans
 * @param {object} options { claimTiers, constraints }
 * @returns {{plans: Array<object>, rejected: Array<{plan_id: string, problems: string[]}>, notes: Array<{plan_id: string, notes: string[]}>}}
 */
export function vetPlans(candidates, options = {}) {
  const { claimTiers = null, constraints = null, hypotheses = [], personaIds = null, excludedSegments = [] } = options;
  const hypothesisById = new Map(hypotheses.map((hypothesis) => [hypothesis.hypothesis_id, hypothesis]));
  const plans = [];
  const rejected = [];
  const notes = [];
  const fulfilled = [];
  const deferred = [];

  for (const candidate of candidates ?? []) {
    const hypothesis = hypothesisById.get(candidate?.hypothesis_id);
    const currentTier = claimTiers?.[candidate?.hypothesis_id];
    if (["validated", "falsified", "abandoned"].includes(hypothesis?.status)) {
      fulfilled.push({ plan_id: candidate?.plan_id ?? "unknown", hypothesis_id: candidate?.hypothesis_id ?? null, status: "retired_by_evidence", achieved_tier: currentTier ?? "E0" });
      continue;
    }
    if (currentTier && tierOrdinal(currentTier) >= tierOrdinal(candidate?.target_evidence_level)) {
      fulfilled.push({ plan_id: candidate?.plan_id ?? "unknown", hypothesis_id: candidate?.hypothesis_id ?? null, status: "retired_by_evidence", achieved_tier: currentTier });
      continue;
    }
    const problems = [
      ...checkMethodFit(candidate, hypothesis),
      ...checkEvidenceUpgrade(candidate, claimTiers, hypothesis),
      ...checkParticipantForeignKeys(candidate, personaIds),
      ...checkQuestionContent(candidate),
      ...checkFalsifiability(candidate),
    ];
    if (!hasQuantifiedThreshold(candidate)) problems.push("success_threshold must contain a quantitative value, duration, amount, ratio, or explicit saturation condition");

    if (problems.length > 0) {
      rejected.push({ plan_id: candidate?.plan_id ?? "unknown", problems });
      continue;
    }

    const sampleNotes = checkSampleSize(candidate);
    if (sampleNotes.length > 0) notes.push({ plan_id: candidate.plan_id, notes: sampleNotes });

    let plan = structuredClone(candidate);
    if (sampleNotes.length > 0) {
      plan.sample_size = { ...plan.sample_size, underpowered: true };
      plan.risks_and_limits = [...(plan.risks_and_limits ?? []), ...sampleNotes];
      plan.estimated_cost = { ...plan.estimated_cost, confidence: "low" };
    } else if (plan.sample_size) {
      plan.sample_size = { ...plan.sample_size, underpowered: false };
    }
    plan.recruitment_criteria = {
      ...plan.recruitment_criteria,
      exclusion: [...new Set([...(plan.recruitment_criteria?.exclusion ?? []), ...excludedSegments])],
    };
    plan = enforceHumanReview(plan);
    plan = annotateFeasibility(plan, constraints);
    if (plan.duration?.fits_constraints === false) {
      deferred.push({ plan_id: plan.plan_id, hypothesis_id: plan.hypothesis_id, reason: plan.duration.note, constraint_gap: plan.duration.note });
      continue;
    }
    plans.push(plan);
  }

  // KB-USR-S6: 1-3 plans, priority ordered.
  const ordered = plans
    .slice()
    .sort((a, b) => (a.priority_rank ?? 99) - (b.priority_rank ?? 99))
    .slice(0, 3)
    .map((plan, index) => ({ ...plan, priority_rank: index + 1 }));

  return { plans: ordered, rejected, notes, fulfilled, deferred };
}

/**
 * A-12: every open hypothesis needs a plan or an explicit deferral.
 * An unlinked open assumption is the failure mode this whole skill exists to
 * prevent, so it is a contract error rather than a warning.
 */
export function linkHypothesesToPlans(hypotheses, plans) {
  const byHypothesis = new Map();
  for (const plan of plans ?? []) {
    const list = byHypothesis.get(plan.hypothesis_id) ?? [];
    list.push(plan.plan_id);
    byHypothesis.set(plan.hypothesis_id, list);
  }

  const linked = (hypotheses ?? []).map((hypothesis) => ({
    ...hypothesis,
    linked_plan_ids: byHypothesis.get(hypothesis.hypothesis_id) ?? [],
  }));

  const orphans = linked.filter(
    (hypothesis) =>
      ["open", "partially_validated"].includes(hypothesis.status) &&
      hypothesis.linked_plan_ids.length === 0 &&
      !(typeof hypothesis.deferred_reason === "string" && hypothesis.deferred_reason.trim().length > 0),
  );

  return { hypotheses: linked, orphans: orphans.map((hypothesis) => hypothesis.hypothesis_id) };
}
