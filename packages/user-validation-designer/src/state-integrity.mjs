import { createHash } from "node:crypto";

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalize(value[key])]));
  }
  return value;
}

export function stableHash(value) {
  return createHash("sha256").update(JSON.stringify(canonicalize(value)), "utf8").digest("hex");
}

export function canonicalTrustedStateMaterial(structured) {
  const material = structuredClone(structured ?? {});
  material.run_manifest ??= {};
  // The digest cannot include itself. No other structured-output field is
  // declared volatile: evidence_recheck can inherit or consult all of it.
  material.run_manifest.state_hash = null;
  return material;
}

export function computeStateHash(structured) {
  return stableHash(canonicalTrustedStateMaterial(structured));
}

export function canonicalRegressionBaselineMaterial(previous) {
  return {
    task_id: previous?.task_id ?? null,
    project_id: previous?.project_id ?? null,
    product_version: previous?.product_version ?? null,
    product_tasks_hash: previous?.product_tasks_hash ?? null,
    scoring_schema_version: previous?.scoring_schema_version ?? null,
    hypotheses: previous?.hypotheses ?? [],
    personas_digest: previous?.personas_digest ?? null,
    task_results: previous?.task_results ?? null,
    experience_issue_ids: previous?.experience_issue_ids ?? null,
  };
}

export function computeRegressionBaselineHash(previous) {
  return stableHash(canonicalRegressionBaselineMaterial(previous));
}
