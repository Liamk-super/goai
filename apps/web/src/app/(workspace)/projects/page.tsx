"use client";

import { useEffect, useState } from "react";
import { browserApi, type Project } from "../../../lib/api-client";
import { PageHeader, StatusPill } from "../../../components/shell/AppShell";
import { useI18n } from "../../../components/i18n/LocaleProvider";

export default function ProjectsPage() {
  const { t } = useI18n();
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    void Promise.resolve().then(() => browserApi().listProjects()).then(result => setProjects(result.items))
      .catch(cause => setError(cause instanceof Error ? cause.message : t("Project signal unavailable")))
      .finally(() => setLoading(false));
  }, [t]);
  return <main>
    <PageHeader eyebrow={t("Signal atlas / 01")} title={t("Projects in motion.")} description={t("Each dossier is a living chain of versions, evidence, decisions and unresolved questions—not a folder of stale reports.")} action={<a className="button" href="/projects/new">{t("Open new signal")}</a>} />
    <section className="metric-row reveal" aria-label={t("Workspace metrics")}>
      <div className="metric"><small>{t("Visible projects")}</small><strong>{loading ? "—" : projects.length}</strong></div>
      <div className="metric"><small>{t("State source")}</small><strong>Postgres</strong></div>
      <div className="metric"><small>{t("Default posture")}</small><strong>{t("Read only")}</strong></div>
      <div className="metric"><small>{t("Evidence policy")}</small><strong>{t("Traceable")}</strong></div>
    </section>
    {error && <p role="alert">{error}</p>}
    {!loading && projects.length === 0 ? <div className="empty-state reveal"><strong>{t("No product signal yet.")}</strong><p>{t("Create a dossier, upload the first material, then let the gaps shape the evaluation.")}</p><a className="button" href="/projects/new">{t("Create first project")}</a></div> :
      <section className="signal-grid reveal">{projects.map((project, index) => <a className="project-card" key={project.project_id} href={`/projects/${project.project_id}`}>
        <span className="number">{t("PROJECT")} / {String(index + 1).padStart(2, "0")}</span><h2>{project.name}</h2><p>{t("Durable dossier · workspace {workspace}", { workspace: project.workspace_id.slice(0, 8) })}</p><StatusPill value={project.status} />
      </a>)}</section>}
  </main>;
}
