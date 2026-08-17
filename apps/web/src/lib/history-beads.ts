import type { EvaluationHistoryItem, Project, Run } from "./api-client";

export type HistoryBead = {
  runId: string;
  projectId: string;
  name: string;
  version: string;
  status: string;
  signal: string;
  updatedAt: string;
};

export function historyItemToBead(item: EvaluationHistoryItem): HistoryBead {
  return {
    runId: item.run_id,
    projectId: item.project_id,
    name: item.project_name,
    version: item.product_version_label || (item.product_version_number ? `V${item.product_version_number}` : "—"),
    status: item.status,
    signal: beadSignal(item.status),
    updatedAt: item.updated_at,
  };
}

/** 珠子 = PostgreSQL Project/Run 历史的视觉投影。不在本地复制业务事实。 */
export function buildHistoryBeads(
  projects: Project[],
  runsByProject: Record<string, Run[]>,
  limit = 6,
): HistoryBead[] {
  const projectById = new Map(projects.map(project => [project.project_id, project]));
  return Object.entries(runsByProject)
    .flatMap(([projectId, runs]) => runs.map((run, index) => ({
      run,
      projectId,
      version: run.product_version_label
        || (run.product_version_number ? `V${run.product_version_number}` : `V${runs.length - index}`),
    })))
    .filter(item => projectById.has(item.projectId))
    .sort((left, right) => (right.run.updated_at ?? "").localeCompare(left.run.updated_at ?? ""))
    .slice(0, Math.max(0, limit))
    .map(({ run, projectId, version }) => ({
      runId: run.run_id,
      projectId,
      name: projectById.get(projectId)!.name,
      version,
      status: run.status,
      signal: beadSignal(run.status),
      updatedAt: run.updated_at ?? "",
    }));
}

export function beadSignal(status: string): string {
  switch (status) {
    case "COMPLETED":
    case "VALIDATED":
      return "已完成";
    case "RUNNING":
      return "预测中";
    case "WAITING_FOR_USER":
      return "等待回答";
    case "PLANNED":
      return "已规划";
    case "FAILED":
    case "BLOCKED":
      return "需关注";
    default:
      return "档案";
  }
}

export function beadTone(status: string): "completed" | "attention" | "idle" {
  if (status === "COMPLETED" || status === "VALIDATED") return "completed";
  if (status === "FAILED" || status === "BLOCKED" || status === "WAITING_FOR_USER") return "attention";
  return "idle";
}
