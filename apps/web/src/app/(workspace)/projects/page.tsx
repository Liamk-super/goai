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
    <PageHeader eyebrow="势能引擎 / PROJECT ATLAS" title="让产品在投入之前，经得起证据。" description="创建产品档案，补齐关键事实，让 1+5 Agent 按同一标准完成评审、证据校准与版本复验。" action={<a className="button" href="/projects/new">创建项目</a>} />
    <section className="metric-row reveal" aria-label={t("Workspace metrics")}>
      <div className="metric"><small>{t("Visible projects")}</small><strong>{loading ? "—" : projects.length}</strong></div>
      <div className="metric"><small>{t("State source")}</small><strong>Postgres</strong></div>
      <div className="metric"><small>{t("Default posture")}</small><strong>{t("Read only")}</strong></div>
      <div className="metric"><small>{t("Evidence policy")}</small><strong>{t("Traceable")}</strong></div>
    </section>
    {error && <p role="alert">{error}</p>}
    {!loading && projects.length === 0 ? <div className="first-project reveal"><div className="empty-wheel"><span>产品材料</span><span>团队信息</span><span>用户经营</span><span>时间地域</span><div><small>证据驱动产品验证</small><strong>势能引擎</strong><p>从一个可复验的产品档案开始</p><a className="button" href="/projects/new">创建第一个项目</a></div></div><aside><p className="panel-kicker">从这里开始</p><h2>不是算一个“爆款概率”。</h2><p>系统会先确认它对产品的理解，再判断资料是否足以启动评审。事实不足时，只追问最影响结论的 3—5 个问题。</p></aside></div> :
      <section className="signal-grid reveal">{projects.map((project, index) => <a className="project-card" key={project.project_id} href={`/projects/${project.project_id}`}>
        <span className="number">{t("PROJECT")} / {String(index + 1).padStart(2, "0")}</span><h2>{project.name}</h2><p>{t("Durable dossier · workspace {workspace}", { workspace: project.workspace_id.slice(0, 8) })}</p><StatusPill value={project.status} />
      </a>)}</section>}
  </main>;
}
