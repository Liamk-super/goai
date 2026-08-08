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
    void Promise.resolve()
      .then(() => browserApi().listProjects())
      .then((result) => setProjects(result.items))
      .catch((cause) =>
        setError(cause instanceof Error ? cause.message : t("Project signal unavailable")),
      )
      .finally(() => setLoading(false));
  }, [t]);

  return (
    <main className="workspace-main">
      <PageHeader
        eyebrow="势能引擎 / PROJECT ATLAS"
        title="让产品在投入之前，经得起证据。"
        description="创建产品档案，补齐关键事实，让 1+5 Agent 按同一标准完成评审、证据校准与版本复验。"
        action={
          <a className="button" href="/projects/new">
            创建项目
          </a>
        }
      />

      {/* 读数条，不做卡片行 */}
      <div className="grid-auto enters" aria-label={t("Workspace metrics")}>
        <dl className="readout">
          <dt>{t("Visible projects")}</dt>
          <dd>{loading ? "—" : projects.length}</dd>
        </dl>
        <dl className="readout">
          <dt>{t("State source")}</dt>
          <dd>Postgres</dd>
        </dl>
        <dl className="readout">
          <dt>{t("Default posture")}</dt>
          <dd>{t("Read only")}</dd>
        </dl>
        <dl className="readout">
          <dt>{t("Evidence policy")}</dt>
          <dd>{t("Traceable")}</dd>
        </dl>
      </div>

      {error && <p role="alert">{error}</p>}

      {!loading && projects.length === 0 ? (
        <section className="plate enters" style={{ marginTop: 40 }}>
          <p className="plate-kicker">从这里开始</p>
          <h2>不是算一个“爆款概率”。</h2>
          <p>
            系统会先确认它对产品的理解，再判断资料是否足以启动评审。事实不足时，只追问最影响结论的
            3—5 个问题。
          </p>
          <p style={{ marginTop: 24 }}>
            <a className="button" href="/projects/new">
              创建第一个项目
            </a>
          </p>
        </section>
      ) : (
        <section className="enters" style={{ marginTop: 40 }}>
          <p className="plate-kicker">项目档案</p>
          <ul className="record-list">
            {projects.map((project, index) => (
              <li key={project.project_id}>
                <span>
                  <span className="bearing">
                    {t("PROJECT")} / {String(index + 1).padStart(2, "0")}
                  </span>
                  <a href={`/projects/${project.project_id}`} style={{ fontSize: 18 }}>
                    {project.name}
                  </a>
                </span>
                <StatusPill value={project.status} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
