"use client";

import { use, useEffect, useState } from "react";
import { browserApi, type AgentTeamsRun, type Project, type ProjectPortrait, type Run } from "../../../../lib/api-client";
import { MomentumWorkbench } from "../../../../components/workspace/MomentumWorkbench";
import { useI18n } from "../../../../components/i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../../../../components/i18n/LocalizedErrorMessage";

export default function ProjectPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { t } = useI18n();
  const { projectId } = use(params);
  const [runs, setRuns] = useState<Run[]>([]);
  const [project, setProject] = useState<Project>();
  const [portrait, setPortrait] = useState<ProjectPortrait>();
  const [team, setTeam] = useState<AgentTeamsRun>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    const api = browserApi();
    void Promise.all([api.listProjects(), api.listRuns(projectId)])
      .then(async ([projects, runResult]) => {
        setProject(projects.items.find(item => item.project_id === projectId));
        setRuns(runResult.items);
        void api.getProjectPortrait(projectId).then(setPortrait).catch(() => undefined);
        if (runResult.items[0] && runResult.items[0].status !== "PLANNED") {
          setTeam(await api.getAgentTeamsRun(runResult.items[0].run_id));
        }
      })
      .catch(cause => setError(cause instanceof Error ? cause.message : t("Project signal unavailable")));
  }, [projectId, t]);

  const completedRuns = runs.filter(run => run.status === "COMPLETED");
  const predictionHref = portrait?.product_version_id && runs.length === 0
    ? `/projects/${projectId}/new-evaluation?versionId=${encodeURIComponent(portrait.product_version_id)}`
    : `/projects/${projectId}/new-evaluation`;
  const predictionLabel = runs.length > 0
    ? t("Submit new version")
    : portrait
      ? t("Start prediction")
      : t("Add details");

  return (
    <main className="workspace-main workspace-main-tall">
      <header className="workspace-toolbar">
        <a className="toolbar-back" href="/projects">
          ← {t("All projects")}
        </a>
        <nav className="toolbar-nav" aria-label={t("Project actions")}>
          <a href={predictionHref}>{predictionLabel}</a>
          {completedRuns.length > 1 && (
            <a href={`/projects/${projectId}/compare/${completedRuns[0].run_id}`}>{t("Compare versions")}</a>
          )}
        </nav>
      </header>
      {error && <LocalizedErrorMessage value={error} />}
      {project && <MomentumWorkbench project={project} runs={runs} team={team} portrait={portrait} />}
    </main>
  );
}
