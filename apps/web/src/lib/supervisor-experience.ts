import type { Report, Run } from "./api-client.ts";

export const SUPERVISOR_STAGES = [
  { ordinal: 1, code: "UNDERSTANDING", label: "正在了解项目", description: "正在检查你提交的产品信息……" },
  { ordinal: 2, code: "MULTI_REVIEW", label: "多维预测", description: "多个专业视角正在独立分析……" },
  { ordinal: 3, code: "REVIEW_REPORT", label: "证据校准与结果汇总", description: "正在核对结论与证据……" },
  { ordinal: 4, code: "COMPLETED", label: "预测完成", description: "报告已经生成。" },
] as const;

export type ExecutionMode = "RECORDED" | "MATERIAL" | "LIVE" | "UNSPECIFIED";

export function executionMode(): ExecutionMode {
  const value = process.env.NEXT_PUBLIC_LAUNCHSCOPE_EXECUTION_MODE?.toUpperCase();
  return value === "RECORDED" || value === "MATERIAL" || value === "LIVE" ? value : "UNSPECIFIED";
}

export function supervisorAdmissionEnabled(): boolean {
  return (
    process.env.NEXT_PUBLIC_LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED === "true" && executionMode() !== "RECORDED"
  );
}

export function isSupervisorExperience(run: Run | undefined): boolean {
  return run?.ui_mode === "SUPERVISOR_1P4" && isSupervisorGeneration(run.architecture_generation);
}

function isSupervisorGeneration(value: string | undefined): boolean {
  return value?.startsWith("supervisor-1p4-") ?? false;
}

export function supervisorProgress(run: Run) {
  const current = run.experience_stage;
  if (!current) throw new Error("Supervisor Run is missing its durable experience_stage projection.");
  return SUPERVISOR_STAGES.map(stage => ({
    ...stage,
    state: stage.ordinal < current.ordinal ? "complete" : stage.ordinal === current.ordinal ? "current" : "upcoming",
  }));
}

export type SupervisorControlAction = "PAUSE" | "RESUME" | "RECOVER" | "STATUS";

export function supervisorControlAction(run: Run): SupervisorControlAction {
  const control = run.execution_control;
  if (run.status === "NEEDS_ATTENTION" && control?.state !== "CLOSED") return "RECOVER";
  if (
    !control
    || control.state === "CLOSED"
    || run.status !== "RUNNING"
  ) return "STATUS";
  if (control.state === "PAUSED") return "RESUME";
  if (control.state === "ACTIVE") return "PAUSE";
  return "STATUS";
}

export function hasLayeredSupervisorReport(report: Report | undefined): boolean {
  return isSupervisorGeneration(report?.architecture_generation) && Boolean(report?.layered_report);
}
