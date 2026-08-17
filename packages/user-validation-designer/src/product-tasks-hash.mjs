/**
 * product_tasks_hash — cross-skill normalization contract (A-29).
 *
 * Spec: docs/PRODUCT_TASKS_HASH_V0.1.md. Frozen by DECISIONS D-05.
 *
 * The same task set must hash identically no matter which skill processes it
 * (`product_tasks` here, `audit.core_tasks` in product-technical-audit).
 * Otherwise V1/V2 comparability fails SILENTLY — no error, just conclusions
 * that look comparable and are not.
 *
 * Do not "optimize" this function. Any change invalidates every stored hash;
 * see the change discipline section of the spec.
 */

import { createHash } from "node:crypto";

const HASH_FIELDS = Object.freeze(["task_key", "description", "expected_observable_outcome"]);

export class TaskHashError extends Error {
  constructor(code) {
    super(code);
    this.name = "TaskHashError";
    this.code = code;
  }
}

/**
 * NFC -> trim -> collapse internal whitespace.
 * No case folding (task_key case is semantic) and no punctuation folding
 * (CJK/ASCII punctuation differences can change a task's meaning).
 */
function normalizeText(value) {
  if (typeof value !== "string") throw new TaskHashError("invalid_task_for_hash");
  const normalized = value.normalize("NFC").trim().replace(/\s+/gu, " ");
  if (normalized.length === 0) throw new TaskHashError("invalid_task_for_hash");
  return normalized;
}

/**
 * @param {Array<object>|null|undefined} tasks
 * @returns {string|null} 64-char lowercase hex, or null when there is no baseline.
 *   null means "no task baseline" and is deliberately NOT the hash of an empty
 *   array, so an absent script cannot be mistaken for an empty one.
 */
export function productTasksHash(tasks) {
  if (!Array.isArray(tasks) || tasks.length === 0) return null;

  const canonical = tasks.map((task) => {
    const entry = {};
    for (const field of HASH_FIELDS) entry[field] = normalizeText(task?.[field]);
    return entry;
  });

  canonical.sort((a, b) => (a.task_key < b.task_key ? -1 : a.task_key > b.task_key ? 1 : 0));

  for (let i = 1; i < canonical.length; i += 1) {
    if (canonical[i].task_key === canonical[i - 1].task_key) {
      throw new TaskHashError("duplicate_task_key");
    }
  }

  return createHash("sha256").update(JSON.stringify(canonical), "utf8").digest("hex");
}

/**
 * Compare the current task set against the previous round's recorded hash.
 * @returns {{match: boolean, current: string|null, previous: string|null, reason: string}}
 */
export function compareTaskBaseline(tasks, previousHash) {
  let current = null;
  try {
    current = productTasksHash(tasks);
  } catch (error) {
    return {
      match: false,
      current: null,
      previous: previousHash ?? null,
      reason: `current task set cannot be hashed: ${error.code}`,
    };
  }

  if (current === null) {
    return { match: false, current, previous: previousHash ?? null, reason: "no task baseline in this round" };
  }
  if (!previousHash) {
    return { match: false, current, previous: null, reason: "previous round recorded no task baseline" };
  }
  return {
    match: current === previousHash,
    current,
    previous: previousHash,
    reason: current === previousHash ? "same task baseline" : "task baseline changed between rounds",
  };
}
