"use client";

import { use, useEffect, useState } from "react";
import { browserApi, type AgentTeamsRun, type Project, type Run } from "../../../../lib/api-client";
import { MomentumWorkbench } from "../../../../components/workspace/MomentumWorkbench";
import { useI18n } from "../../../../components/i18n/LocaleProvider";

export default function ProjectPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { t } = useI18n();
  const { projectId } = use(params); const [runs, setRuns] = useState<Run[]>([]); const [project, setProject] = useState<Project>(); const [team, setTeam] = useState<AgentTeamsRun>(); const [error, setError] = useState<string>();
  useEffect(() => { const api = browserApi(); void Promise.all([api.listProjects(), api.listRuns(projectId)]).then(async ([projects, runResult]) => {
    setProject(projects.items.find(item => item.project_id === projectId)); setRuns(runResult.items);
    if (runResult.items[0] && runResult.items[0].status !== "PLANNED") setTeam(await api.getAgentTeamsRun(runResult.items[0].run_id));
  }).catch(cause => setError(cause instanceof Error ? cause.message : t("Project signal unavailable"))); }, [projectId, t]);
  return <main className="workspace-main"><header className="workspace-toolbar"><div><p>势能引擎 · 项目操作台</p><strong>{project?.name ?? "加载项目…"}</strong></div><nav><a href="/projects">切换项目</a><a href={`/projects/${projectId}/new-evaluation`}>提交新版本</a>{runs.length > 1 && <a href={`/projects/${projectId}/compare/${runs[0].run_id}`}>V1 / V2 对比</a>}</nav></header>{error && <p role="alert">{error}</p>}{project && <MomentumWorkbench project={project} runs={runs} team={team} />}</main>;
}
