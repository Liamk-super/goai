"use client";

import { use, useEffect, useState } from "react";

import { useI18n } from "../../../../../../../components/i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../../../../../../../components/i18n/LocalizedErrorMessage";
import { PublicDemoShell } from "../../../../../../../components/reports/PublicDemoShell";
import { StatusPill } from "../../../../../../../components/shell/AppShell";
import { apiBase, type AgentTeamsRun, type Report, type Run } from "../../../../../../../lib/api-client";
import { supervisorProgress } from "../../../../../../../lib/supervisor-experience";

export default function PublicDemoRunPage({
  params,
}: {
  params: Promise<{ token: string; runId: string }>;
}) {
  const { token, runId } = use(params);
  const { t, status } = useI18n();
  const [run, setRun] = useState<Run>();
  const [team, setTeam] = useState<AgentTeamsRun>();
  const [report, setReport] = useState<Report>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    const query = `?token=${encodeURIComponent(token)}`;
    void Promise.all([
      fetch(`${apiBase()}/api/v1/public/demo/runs/${runId}${query}`),
      fetch(`${apiBase()}/api/v1/public/demo/runs/${runId}/agentteams${query}`),
    ]).then(async ([runResponse, teamResponse]) => {
      if (!runResponse.ok || !teamResponse.ok) throw new Error(t("This read-only Run link is invalid or revoked."));
      const [runValue, teamValue] = await Promise.all([
        runResponse.json() as Promise<Run>,
        teamResponse.json() as Promise<AgentTeamsRun>,
      ]);
      setRun(runValue);
      setTeam(teamValue);
    }).catch(cause => setError(cause instanceof Error ? cause.message : t("Run loading failed")));
  }, [runId, token]);

  useEffect(() => {
    if (!run) return;
    void fetch(`${apiBase()}/api/v1/public/demo/reports/9027a64b-f6e5-40ad-a31b-c161a0f8724e?token=${encodeURIComponent(token)}`)
      .then(response => response.ok ? response.json() as Promise<Report> : Promise.reject(new Error(t("Report unavailable"))))
      .then(value => value.run_id === runId ? setReport(value) : setError(t("The shared report does not match this Run.")))
      .catch(cause => setError(cause instanceof Error ? cause.message : t("Report loading failed")));
  }, [run, runId, token]);

  return (
      <PublicDemoShell>
        {error && <main className="workspace-main"><LocalizedErrorMessage value={error} className="error-banner" /></main>}
        {!error && !run && <main className="workspace-main"><div className="empty-state"><strong>{t("Reading sealed Run…")}</strong></div></main>}
        {run && (
          <main className="workspace-main workspace-main-tall supervisor-run-page">
            <div className="page-head page-head-compact">
              <div className="page-head-row">
                <div><span className="bearing">{t("Project lead evaluation · {id}", { id: run.run_id.slice(0, 8) })}</span><h1>{t("The project lead completed this evaluation")}</h1><p>{t("The public read-only view shows only sealed state, task results, and the final report.")}</p></div>
                <StatusPill value={run.status} />
              </div>
            </div>
            <section className="plate supervisor-progress-plate">
              <div className="page-head-row"><div><p className="plate-kicker">{t("Prediction progress")}</p><h2>{t("Four-stage prediction")}</h2></div><span className="bearing">4 / 4</span></div>
              <ol className="supervisor-progress">{supervisorProgress(run).map(item => <li key={item.code} data-state={item.state}><span className="supervisor-progress-index">0{item.ordinal}</span><span>{t(item.label)}</span></li>)}</ol>
            </section>
            {report && <section className="plate supervisor-report-ready"><p className="plate-kicker">{t("Prediction result")}</p><h2>{t("Your prediction result is ready")}</h2><p>{t("This result combines product, user, business, and market evidence. Missing evidence is shown clearly instead of being guessed.")}</p><a className="button" href={`/shared/demo/${token}/reports/${report.report_id}`}>{t("View final report without signing in")}</a></section>}
            <details className="plate plate-quiet supervisor-process-details">
              <summary><span>{t("View real Agent process")}</span><span className="g-meta">{t("Collapsed by default · read only")}</span></summary>
              <div className="grid-auto supervisor-technical-readouts"><dl className="readout"><dt>{t("Control-plane stage")}</dt><dd>{run.current_stage ? status(run.current_stage) : t("Awaiting request")}</dd></dl><dl className="readout"><dt>{t("Contract generation")}</dt><dd>{run.architecture_generation}</dd></dl><dl className="readout"><dt>{t("State cursor")}</dt><dd>{run.current_cursor}</dd></dl></div>
              <ul className="record-list supervisor-task-list">{team?.tasks.map(item => <li key={item.id}><span><strong>{item.agent_identity_ref.split("@")[0].replaceAll("-", " ")}</strong><span className="bearing">{item.stage_code.replaceAll("_", " ")}</span></span><StatusPill value={item.status} /></li>)}</ul>
            </details>
          </main>
        )}
      </PublicDemoShell>
  );
}
