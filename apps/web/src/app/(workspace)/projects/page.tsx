"use client";

import { useEffect, useMemo, useState } from "react";
import { browserApi, type EvaluationHistoryItem, type Project } from "../../../lib/api-client";
import { filterProjects } from "../../../lib/project-history";
import { PageHeader, StatusPill } from "../../../components/shell/AppShell";
import { useI18n } from "../../../components/i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../../../components/i18n/LocalizedErrorMessage";

export default function ProjectsPage() {
  const { t } = useI18n();
  const [projects, setProjects] = useState<Project[]>([]);
  const [history, setHistory] = useState<EvaluationHistoryItem[]>([]);
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [projectSearch, setProjectSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");

  useEffect(() => {
    void Promise.all([
      browserApi().listProjects(),
      browserApi().listEvaluationHistory({ limit: 50 }),
    ])
      .then(([projectResult, historyResult]) => {
        setProjects(projectResult.items);
        setHistory(historyResult.items);
      })
      .catch((cause) =>
        setError(cause instanceof Error ? cause.message : t("Project signal unavailable")),
      )
      .finally(() => setLoading(false));
  }, [t]);

  const visibleProjects = useMemo(
    () => filterProjects(projects, projectSearch).filter(project => statusFilter === "ALL" || project.status === statusFilter),
    [projectSearch, projects, statusFilter],
  );
  const latestByProject = useMemo(() => {
    const latest = new Map<string, EvaluationHistoryItem>();
    history.forEach(item => { if (!latest.has(item.project_id)) latest.set(item.project_id, item); });
    return latest;
  }, [history]);
  const projectStatuses = useMemo(() => Array.from(new Set(projects.map(project => project.status))).sort(), [projects]);

  return (
    <main className="workspace-main">
      <PageHeader
        eyebrow={t("Signal atlas / 01")}
        title={t("Projects in motion.")}
        description={t("Each dossier is a living chain of versions, evidence, decisions and unresolved questions—not a folder of stale reports.")}
        action={<a className="button" href="/?start=1">{t("Open new signal")}</a>}
      />

      {error && <LocalizedErrorMessage value={error} />}

      {loading && (
        <div className="empty-state" aria-live="polite">
          <strong>{t("Loading projects…")}</strong>
        </div>
      )}

      {!loading && projects.length > 0 && (
        <section className="project-archive enters">
          <div className="project-archive-heading">
            <div>
              <p className="plate-kicker">{t("My projects")}</p>
              <p>{t("Search projects by name, then open one to browse its prediction versions.")}</p>
            </div>
            <span>{t("Showing {count} of {total} projects", { count: visibleProjects.length, total: projects.length })}</span>
          </div>
          <div className="project-archive-filters">
            <label className="archive-search">
              <span className="field-name">{t("Find a project")}</span>
              <span className="archive-search-control">
              <input
                type="search"
                value={projectSearch}
                onChange={event => setProjectSearch(event.target.value)}
                placeholder={t("Search by project name")}
              />
              {projectSearch && (
                <button type="button" className="quiet" onClick={() => setProjectSearch("")}>
                  {t("Clear search")}
                </button>
              )}
              </span>
            </label>
            <label><span className="field-name">{t("Project status")}</span><select value={statusFilter} onChange={event => setStatusFilter(event.target.value)}><option value="ALL">{t("All statuses")}</option>{projectStatuses.map(value => <option key={value} value={value}>{t(value)}</option>)}</select></label>
          </div>
          <ul className="record-list project-archive-records">
            {visibleProjects.map((project, index) => {
              const latest = latestByProject.get(project.project_id);
              return (
              <li key={project.project_id}>
                <span>
                  <span className="bearing">{t("PROJECT")} / {String(index + 1).padStart(2, "0")}</span>
                  <a href={`/projects/${project.project_id}`} className="project-archive-link">{project.name}</a>
                  <span className="project-archive-meta">{latest ? `${latest.product_version_label ?? t("Version") } · ${latest.recommendation ? t(latest.recommendation) : t(latest.status)} · ${new Date(latest.updated_at).toLocaleDateString()}` : t("No completed evaluation yet")}</span>
                </span>
                <StatusPill value={project.status} />
              </li>
              );
            })}
          </ul>
          {visibleProjects.length === 0 && (
            <div className="archive-search-empty" role="status">
              <strong>{t("No matching projects.")}</strong>
              <button type="button" className="quiet" onClick={() => setProjectSearch("")}>
                {t("Clear search")}
              </button>
            </div>
          )}
        </section>
      )}

      {!loading && projects.length === 0 && (
        <section className="plate enters project-archive-empty">
          <p className="plate-kicker">{t("My projects")}</p>
          <h2>{t("No product signal yet.")}</h2>
          <p>{t("Create a dossier, upload the first material, then let the gaps shape the evaluation.")}</p>
          <p className="project-archive-empty-action">
            <a className="button" href="/?start=1">{t("Create first project")}</a>
          </p>
        </section>
      )}
    </main>
  );
}
