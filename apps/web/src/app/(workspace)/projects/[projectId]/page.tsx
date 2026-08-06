"use client";

import { use, useEffect, useState } from "react";
import { browserApi, type Run } from "../../../../lib/api-client";
import { PageHeader, StatusPill } from "../../../../components/shell/AppShell";
import { useI18n } from "../../../../components/i18n/LocaleProvider";

export default function ProjectPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { t } = useI18n();
  const { projectId } = use(params); const [runs, setRuns] = useState<Run[]>([]); const [error, setError] = useState<string>();
  useEffect(() => { void browserApi().listRuns(projectId).then(result => setRuns(result.items)).catch(cause => setError(cause.message)); }, [projectId]);
  return <main><PageHeader eyebrow={t("Dossier / {id}", { id: projectId.slice(0, 8) })} title={t("A thesis under pressure.")} description={t("Versions share one project identity and one evidence standard. Every run adds history; none erase it.")} action={<a className="button" href={`/projects/${projectId}/new-evaluation`}>{t("New evaluation")}</a>} />
    {error && <p role="alert">{error}</p>}
    <section className="panel reveal"><div className="panel-header"><div><p className="panel-kicker">{t("Run ledger")}</p><h2>{t("Durable evaluations")}</h2></div><span>{t("{count} total", { count: runs.length })}</span></div>
      {runs.length === 0 ? <div className="empty-state"><strong>{t("No run has crossed intake.")}</strong><p>{t("Submit one product version to begin the evidence chain.")}</p></div> : <ul className="run-list">{runs.map(run => <li key={run.run_id}><div><a href={`/runs/${run.run_id}`}>{run.run_id}</a><p>{run.current_stage ?? t("Awaiting stage")} · {t("standard {standard}", { standard: run.standard_version })}</p></div><StatusPill value={run.status} /></li>)}</ul>}
    </section>
    {runs.length > 1 && <p><a className="button secondary" href={`/projects/${projectId}/compare/${runs[0].run_id}`}>{t("Compare latest to prior")}</a></p>}
  </main>;
}
