import type { AgentTeamsRun, Run } from "./api-client";

export const FACT_SECTORS = [
  { key: "product", code: "I", name: "产品材料", fields: ["problem", "core_features", "inspectable_materials"] },
  { key: "team", code: "II", name: "团队能力", fields: ["team", "stage"] },
  { key: "market", code: "III", name: "用户与经营", fields: ["target_user", "payer", "validation_goal"] },
  { key: "geo", code: "IV", name: "时间与地域", fields: ["region", "timing"] },
] as const;

export const JUDGMENT_DIMENSIONS = [
  { code: "PRODUCT_IMPLEMENTATION", agent: "product-engineering", name: "产品与交付" },
  { code: "USER_USAGE", agent: "user-evidence", name: "用户证据" },
  { code: "BUSINESS_INVESTMENT", agent: "business-investment", name: "商业与投资" },
  { code: "GEO_POLICY_TREND", agent: "geo-policy-trend", name: "时间、地域与政策" },
] as const;

export type SectorState = {
  key: string;
  code: string;
  name: string;
  filled: number;
  total: number;
};

export type DimensionState = {
  code: string;
  agent: string;
  name: string;
  status: string;
  evidence: number;
};

export type CalibrationState = {
  status: "IDLE" | "CALIBRATING" | "CALIBRATED" | "ATTENTION";
  evidenceTotal: number;
  needsHumanReview: number;
};

export function buildSectorStates(fields: Record<string, string>): SectorState[] {
  return FACT_SECTORS.map(sector => ({
    key: sector.key,
    code: sector.code,
    name: sector.name,
    filled: sector.fields.filter(field => fields[field]?.trim()).length,
    total: sector.fields.length,
  }));
}

export function buildDimensionStates(team: Pick<AgentTeamsRun, "tasks"> | undefined): DimensionState[] {
  return JUDGMENT_DIMENSIONS.map(dimension => {
    const tasks = (team?.tasks ?? []).filter(
      task => task.agent_identity_ref.split("@")[0] === dimension.agent,
    );
    const status =
      tasks.find(task => task.status === "RUNNING" || task.status === "LEASED")?.status
        ?? tasks.find(task => task.status === "NEEDS_INPUT")?.status
        ?? tasks[0]?.status
        ?? "PENDING";
    return {
      code: dimension.code,
      agent: dimension.agent,
      name: dimension.name,
      status,
      evidence: tasks.reduce((total, task) => total + (task.evidence_count ?? 0), 0),
    };
  });
}

export function buildCalibrationState(team: Pick<AgentTeamsRun, "tasks"> | undefined): CalibrationState {
  const tasks = team?.tasks ?? [];
  const auditorTasks = tasks.filter(task => task.agent_identity_ref.split("@")[0] === "evidence-auditor");
  const evidenceTotal = tasks.reduce((total, task) => total + (task.evidence_count ?? 0), 0);
  const needsHumanReview = tasks.filter(task => task.needs_human_review).length;
  const status: CalibrationState["status"] = needsHumanReview > 0
    ? "ATTENTION"
    : auditorTasks.some(task => task.status === "RUNNING" || task.status === "LEASED")
      ? "CALIBRATING"
      : auditorTasks.some(task => task.status === "SUCCEEDED")
        ? "CALIBRATED"
        : "IDLE";
  return { status, evidenceTotal, needsHumanReview };
}

/** 盘面棘轮格数：只被真实证据推动。没有证据，盘面不动。 */
export const NOTCHES_PER_REVOLUTION = 32;

export function notchFromEvidence(evidenceTotal: number): number {
  return Math.max(0, Math.min(evidenceTotal, NOTCHES_PER_REVOLUTION));
}

export function stageReadout(run: Pick<Run, "status" | "current_stage"> | undefined): string {
  if (!run) return "资料收集";
  return (run.current_stage ?? run.status).replaceAll("_", " ");
}
