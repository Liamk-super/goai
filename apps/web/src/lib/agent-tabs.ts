import type { AgentTeamsRun, Clarification } from "./api-client";

/** The four domain specialists. The supervisor and the calibration Agent are not
 *  user-facing tabs: the supervisor owns impact scoping, and calibration audits
 *  evidence rather than asking the user for product facts. */
export const SPECIALIST_AGENTS = [
  { code: "product-engineering", name: "产品经理" },
  { code: "user-evidence", name: "用户" },
  { code: "business-investment", name: "投资人" },
  { code: "geo-policy-trend", name: "时间、地域与政策" },
] as const;

export type AgentTab = {
  code: string;
  name: string;
  status: string;
  evidence: number;
  pending: Clarification[];
};

export const agentCodeOf = (ref: string) => ref.split("@")[0];

export function buildAgentTabs(
  team: Pick<AgentTeamsRun, "tasks"> | undefined,
  questions: Clarification[],
): AgentTab[] {
  return SPECIALIST_AGENTS.map(agent => {
    const tasks = (team?.tasks ?? []).filter(task => agentCodeOf(task.agent_identity_ref) === agent.code);
    const pending = questions.filter(item => item.agent_code === agent.code);
    const status = pending.length
      ? "NEEDS_INPUT"
      : tasks.find(task => task.status === "RUNNING" || task.status === "LEASED")?.status
        ?? tasks.find(task => task.status === "NEEDS_INPUT")?.status
        ?? tasks[0]?.status
        ?? "PENDING";
    return {
      code: agent.code,
      name: agent.name,
      status,
      evidence: tasks.reduce((total, task) => total + (task.evidence_count ?? 0), 0),
      pending,
    };
  });
}
