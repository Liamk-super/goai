/**
 * V1/V2 regression discipline. Implements R-01..R-08 (SKILL_SPEC_V0.1 §7).
 *
 * The threat this module defends against is not a crash — it is a plausible,
 * comparable-looking report built on a changed baseline or a quietly relaxed
 * threshold. Both would let a project claim progress it has not made, so both
 * are mechanical failures here.
 */

import { compareTaskBaseline } from "./product-tasks-hash.mjs";
import { tierOrdinal } from "./rules.mjs";

/** R-01: same task baseline, or the round is not comparable. */
export function checkBaseline(input) {
  const previous = input?.previous_validation_results;
  if (!previous) {
    return { ok: false, failure_reason: "script_mismatch", reason: "version_regression mode requires previous_validation_results" };
  }
  if (!Array.isArray(input?.product_tasks) || input.product_tasks.length === 0) {
    return { ok: false, failure_reason: "script_mismatch", reason: "version_regression mode requires product_tasks" };
  }
  const baseline = compareTaskBaseline(input.product_tasks, previous.product_tasks_hash);
  if (!baseline.match) {
    return { ok: false, failure_reason: "script_mismatch", reason: baseline.reason, baseline };
  }
  return { ok: true, baseline };
}

/**
 * R-02/R-03: carry forward unresolved hypothesis IDs before adding new ones.
 * Re-inventing questions each round is how a project avoids ever answering one.
 *
 * @returns {{required_ids: string[], missing_ids: string[], settled_ids: string[], next_index: number}}
 */
export function checkHypothesisInheritance(previous, currentHypotheses) {
  const previousList = previous?.hypotheses ?? [];
  const required = previousList
    .filter((hypothesis) => ["open", "partially_validated"].includes(hypothesis.status))
    .map((hypothesis) => hypothesis.hypothesis_id);
  const settled = previousList
    .filter((hypothesis) => ["validated", "falsified", "abandoned"].includes(hypothesis.status))
    .map((hypothesis) => hypothesis.hypothesis_id);

  const currentIds = new Set((currentHypotheses ?? []).map((hypothesis) => hypothesis.hypothesis_id));
  const missing = required.filter((id) => !currentIds.has(id));

  const numbers = previousList
    .map((hypothesis) => Number.parseInt(String(hypothesis.hypothesis_id).replace(/^\D+/u, ""), 10))
    .filter((value) => Number.isInteger(value));
  const next_index = numbers.length > 0 ? Math.max(...numbers) + 1 : 1;

  const previousIds = new Set(previousList.map((h) => h.hypothesis_id));
  const invalid_new_ids = (currentHypotheses ?? [])
    .filter((h) => !previousIds.has(h.hypothesis_id))
    .filter((h) => {
      const match = /^H(\d+)$/u.exec(h.hypothesis_id);
      return !match || Number(match[1]) < next_index;
    })
    .map((h) => h.hypothesis_id);
  return { required_ids: required, missing_ids: missing, settled_ids: settled, next_index, invalid_new_ids };
}

function normalizeStatement(value) {
  return String(value ?? "").normalize("NFC").toLowerCase().replace(/[\p{P}\p{S}\s]+/gu, "");
}

export function checkHypothesisIdentity(previous, currentHypotheses) {
  const previousById = new Map((previous?.hypotheses ?? []).map((claim) => [claim.hypothesis_id, claim]));
  const violations = [];
  const reframes = [];
  for (const current of currentHypotheses ?? []) {
    const prior = previousById.get(current.hypothesis_id);
    if (!prior || normalizeStatement(prior.statement) === normalizeStatement(current.statement)) continue;
    const audited = current.standard_changed === true && typeof current.reframe_reason === "string" && current.reframe_reason.trim().length > 0;
    if (audited) reframes.push({ hypothesis_id: current.hypothesis_id, from: prior.statement, to: current.statement, reason: current.reframe_reason });
    else violations.push({ hypothesis_id: current.hypothesis_id, problem: "same hypothesis_id changed semantic statement without explicit reframe_reason and standard_changed=true" });
  }
  return { violations, reframes };
}

export function checkSettledReopen(previous, currentHypotheses, plans) {
  const settled = new Map((previous?.hypotheses ?? []).filter((h) => ["validated", "falsified", "abandoned"].includes(h.status)).map((h) => [h.hypothesis_id, h]));
  const violations = [];
  for (const current of currentHypotheses ?? []) {
    if (!settled.has(current.hypothesis_id)) continue;
    const reopened = ["open", "partially_validated"].includes(current.status);
    const hasPlan = (plans ?? []).some((plan) => plan.hypothesis_id === current.hypothesis_id);
    const audited = current.reopened === true && typeof current.reopen_reason === "string" && current.reopen_reason.trim() && typeof current.transition_driver === "string" && current.transition_driver.trim();
    if ((reopened || hasPlan) && !audited) violations.push({ hypothesis_id: current.hypothesis_id, problem: "settled hypothesis reopened or replanned without explicit reopen reason and audit driver" });
  }
  return violations;
}

export function comparePersonas(previousDigest, currentPersonas) {
  const current = new Map((currentPersonas ?? []).map((p) => [p.persona_id, p]));
  const drift = [];
  for (const prior of previousDigest ?? []) {
    const now = current.get(prior.persona_id);
    if (!now) {
      drift.push({ persona_id: prior.persona_id, change: "removed", before: prior.label, after: null });
      continue;
    }
    const before = JSON.stringify({ label: prior.label, behavior_keys: prior.behavior_keys ?? null });
    const after = JSON.stringify({ label: now.label, behavior_keys: now.behavior_keys ?? null });
    if (before !== after) drift.push({ persona_id: prior.persona_id, change: "semantic_change", before: prior.label, after: now.label });
  }
  return drift;
}

export function compareTasks(previousResults, currentResults) {
  const summarize = (records) => {
    const byTask = new Map();
    for (const record of records ?? []) {
      const list = byTask.get(record.task_key) ?? [];
      list.push(record.result);
      byTask.set(record.task_key, list);
    }
    const order = ["failed", "not_executed", "completed_with_difficulty", "completed"];
    return new Map([...byTask].map(([key, values]) => [key, order.find((status) => values.includes(status)) ?? "not_executed"]));
  };
  const prior = summarize(previousResults);
  const now = summarize(currentResults);
  const rank = { failed: 0, not_executed: 1, completed_with_difficulty: 2, completed: 3 };
  return [...new Set([...prior.keys(), ...now.keys()])].map((task_key) => {
    const result_prev = prior.get(task_key) ?? "not_executed";
    const result_now = now.get(task_key) ?? "not_executed";
    const delta = rank[result_now] > rank[result_prev] ? "improved" : rank[result_now] < rank[result_prev] ? "regressed" : "unchanged";
    return { task_key, result_prev, result_now, threshold_expression: null, threshold_reused: true, delta };
  });
}

/**
 * R-05: a threshold may change, but only with a stated reason.
 * Silent threshold relaxation is the most common way a second round
 * manufactures the appearance of progress.
 */
export function checkThresholdIntegrity(plans, previous) {
  const previousThresholds = new Map(
    (previous?.hypotheses ?? [])
      .filter((hypothesis) => typeof hypothesis.success_threshold === "string")
      .map((hypothesis) => [hypothesis.hypothesis_id, hypothesis.success_threshold]),
  );

  const violations = [];
  const changes = [];

  for (const plan of plans ?? []) {
    const prior = previousThresholds.get(plan.hypothesis_id);
    if (!prior) continue;

    const now = plan?.success_threshold?.expression;
    const reused = plan?.success_threshold?.reused_from_previous_round;
    const reason = plan?.success_threshold?.change_reason;

    if (now !== prior) {
      if (reused === true) {
        violations.push({
          plan_id: plan.plan_id,
          hypothesis_id: plan.hypothesis_id,
          problem: `threshold marked reused but differs from previous round ("${prior}" -> "${now}")`,
        });
      } else if (!(typeof reason === "string" && reason.trim().length > 0)) {
        violations.push({
          plan_id: plan.plan_id,
          hypothesis_id: plan.hypothesis_id,
          problem: `threshold changed without change_reason ("${prior}" -> "${now}")`,
        });
      } else {
        changes.push({ item: `success_threshold:${plan.hypothesis_id}`, from: prior, to: now, reason });
      }
    }
  }
  return { violations, changes };
}

/** R-08 + ledger assembly: what moved, what did not, and why. */
export function buildLedger(previous, currentHypotheses) {
  const previousById = new Map((previous?.hypotheses ?? []).map((h) => [h.hypothesis_id, h]));
  const ledger = [];

  for (const current of currentHypotheses ?? []) {
    const prior = previousById.get(current.hypothesis_id);
    if (!prior) continue;

    const fromTier = tierOrdinal(prior.evidence_level);
    const toTier = tierOrdinal(current.current_evidence_level);

    let transition;
    if (["validated", "falsified"].includes(current.status) && ["open", "partially_validated"].includes(prior.status)) {
      transition = "newly_settled";
    } else if (["validated", "falsified"].includes(prior.status) && current.status === "open") {
      transition = "reopened";
    } else if (toTier > fromTier) {
      transition = "upgraded";
    } else if (toTier < fromTier) {
      transition = "downgraded";
    } else {
      transition = "unchanged";
    }

    ledger.push({
      hypothesis_id: current.hypothesis_id,
      statement_prev: prior.statement,
      statement_now: current.statement,
      status_prev: prior.status,
      status_now: current.status,
      evidence_level_prev: prior.evidence_level,
      evidence_level_now: current.current_evidence_level,
      transition,
      driver: current.transition_driver ?? "not stated",
      evidence_refs: [...new Set([...(current.supporting_refs ?? []), ...(current.contradicting_refs ?? [])])],
      evidence_relations: [
        ...(current.supporting_refs ?? []).map((evidence_id) => ({ evidence_id, relation: "support" })),
        ...(current.contradicting_refs ?? []).map((evidence_id) => ({ evidence_id, relation: "contradict" })),
      ],
    });
  }
  return ledger;
}

/**
 * Final comparability verdict. `incomparable` wins over any apparent movement:
 * if the baseline or the standard changed, the two rounds simply cannot be
 * compared, and saying otherwise would be the failure mode this guards.
 */
export function progressVerdict({ baselineMatch, standardChanged, ledger }) {
  if (!baselineMatch || standardChanged) return "incomparable";
  const list = ledger ?? [];
  if (list.length === 0) return "no_change";
  const upgraded = list.filter((entry) => ["upgraded", "newly_settled"].includes(entry.transition)).length;
  const downgraded = list.filter((entry) => ["downgraded", "reopened"].includes(entry.transition)).length;
  if (upgraded > 0 && downgraded > 0) return "mixed";
  if (downgraded > 0) return "regression";
  if (upgraded > 0) return "real_progress";
  return "no_change";
}
