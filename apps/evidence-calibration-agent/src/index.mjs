import { runAudit } from "../../../packages/evidence-grounding-audit/src/index.mjs";

export const identity = Object.freeze({ code: "evidence-auditor", role: "Evidence Calibration Agent", skill: "evidence-grounding-audit@1.0.0", depends_on: ["product", "user", "investment"] });

export function runAgent(task, options = {}) {
  const result = runAudit(task, options);
  return { agent: identity, status: result.status === "completed" ? "SUCCEEDED" : "BLOCKED", skill_invocation: { skill: identity.skill, completed: result.status === "completed", structured_output_digest: result.structured_output?.structured_output_digest ?? null }, result, supervisor_handoff: result.structured_output?.supervisor_handoff ?? null };
}
