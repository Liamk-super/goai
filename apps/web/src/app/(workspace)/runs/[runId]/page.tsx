"use client";

import { use, useEffect, useState } from "react";
import { apiBase, browserApi, sessionFromDocument, type AgentTeamsRun, type Run } from "../../../../lib/api-client";
import { DurableRunStream, type SseEvent } from "../../../../lib/sse-client";
import { RunTimeline } from "../../../../components/runs/RunTimeline";
import { PageHeader, StatusPill } from "../../../../components/shell/AppShell";
import { useI18n } from "../../../../components/i18n/LocaleProvider";

export default function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { t } = useI18n();
  const { runId } = use(params); const [run, setRun] = useState<Run>(); const [team,setTeam]=useState<AgentTeamsRun>(); const [events, setEvents] = useState<SseEvent[]>([]); const [error, setError] = useState<string>(); const [reportId, setReportId] = useState<string>(); const [busy,setBusy]=useState(false); const [replay,setReplay]=useState(0); const [manifest,setManifest]=useState<string>();
  useEffect(() => { let stream: DurableRunStream | undefined; try { const session = sessionFromDocument(); browserApi().getRun(runId).then(snapshot => { setRun(snapshot); if(snapshot.status === "COMPLETED") void browserApi().getReportForRun(runId).then(report=>setReportId(report.report_id)); }).catch(cause => setError(cause.message)); const query=replay > 0 ? "?cursor=event.initial" : ""; stream = new DurableRunStream(`${apiBase()}/api/v1/runs/${runId}/events${query}`, { "X-Tenant-Id": session.tenantId, "X-Actor-Id": session.actorId, "X-Correlation-Id": crypto.randomUUID() }, { onSnapshot: snapshot => setRun(snapshot as Run), onEvent: event => setEvents(current => current.some(item => item.id === event.id) ? current : [...current, event]), onError: cause => setError(cause.message) }, undefined, async () => browserApi().getRun(runId) as unknown as Record<string, unknown>); void stream.connect(); } catch (cause) { setError(cause instanceof Error ? cause.message : t("No workspace session")); } return () => stream?.stop(); }, [runId,replay,t]);
  useEffect(()=>{if(run?.status==="COMPLETED"&&!reportId){void browserApi().getReportForRun(runId).then(report=>setReportId(report.report_id)).catch(cause=>setError(cause instanceof Error?cause.message:t("Report loading failed")));}},[run?.status,reportId,runId,t]);
  useEffect(()=>{if(!run||run.status==="PLANNED")return; const refresh=()=>void browserApi().getAgentTeamsRun(runId).then(setTeam).catch(cause=>setError(cause instanceof Error?cause.message:t("AgentTeams projection failed"))); refresh(); if(run.status!=="RUNNING")return; const timer=window.setInterval(refresh,3000); return()=>window.clearInterval(timer);},[run?.status,runId,t]);
  async function execute(){setBusy(true);setError(undefined);try{const result=await browserApi().dispatch(runId);setManifest(result.manifest_sha256);setRun(current=>current?{...current,status:result.status,current_stage:"LEADER_PLANNING"}:current);setReplay(value=>value+1);}catch(cause){setError(cause instanceof Error?cause.message:t("Dispatch failed"));}finally{setBusy(false)}}
  return <main className="workspace-main">
    <PageHeader eyebrow={t("Run / {id}", { id: runId.slice(0,8) })} title={t("Evidence in flight.")} description={t("Every pulse below is a durable status fact. Refresh or disconnect: the cursor returns to PostgreSQL, not memory.")} action={run && <StatusPill value={run.status} />} />
    {error && <p role="alert">{error}</p>}

    <div className="grid-auto enters">
      <dl className="readout"><dt>{t("Current stage")}</dt><dd>{run?.current_stage?.replaceAll("_"," ") ?? t("Awaiting")}</dd></dl>
      <dl className="readout"><dt>{t("Events received")}</dt><dd>{events.length}</dd></dl>
      <dl className="readout"><dt>{t("Standard")}</dt><dd>{run?.standard_version ?? "—"}</dd></dl>
      <dl className="readout"><dt>{t("Cursor")}</dt><dd>{run?.current_cursor === "event.initial" ? t("Initial") : t("Durable")}</dd></dl>
    </div>

    {run?.status === "PLANNED" && <section className="plate enters">
      <p className="plate-kicker">{t("AgentTeams v1.2.0 / asynchronous")}</p>
      <h2>{t("Freeze and dispatch.")}</h2>
      <p>冻结 Manifest、预算和 1+5 任务图后再派发。当前 Demo 不要求供应商 usage 回执；生产模式仍会在提交或计费状态未知时停止且不自动重试。</p>
      <dl className="readout"><dt>Budget ceiling</dt><dd>USD 20 · hard limit</dd></dl>
      <div className="form-actions">
        <button onClick={execute} disabled={busy}>{busy?t("Freezing manifest…"):t("Dispatch real AgentTeam")}</button>
      </div>
    </section>}

    {manifest && <dl className="readout"><dt>{t("Frozen manifest")}</dt><dd>{manifest}</dd></dl>}

    {team && <section className="plate enters">
      <p className="plate-kicker">{team.team.agentteams_version} · {team.team.binding_status}</p>
      <div className="page-head-row">
        <h2>1 + 5 Agent 运行面板</h2>
        <span className="bearing">{team.handoff_count} {t("handoffs")} · {team.matrix_event_count} Matrix</span>
      </div>
      {team.budget && <dl className="readout"><dt>{t("Budget")}</dt><dd>${team.budget.consumed} / ${team.budget.limit} USD · {team.budget.status}</dd></dl>}
      <ul className="record-list" style={{ marginTop: 18 }}>
        {team.tasks.map(item=><li key={item.id} style={{ display: "block" }}>
          <div className="page-head-row" style={{ alignItems: "baseline" }}>
            <span>
              <span className="bearing">{item.stage_code.replaceAll("_"," ")}</span>
              <strong style={{ fontSize: 16 }}>{item.agent_identity_ref.split("@")[0].replaceAll("-"," ")}</strong>
            </span>
            <StatusPill value={item.status}/>
          </div>
          <p style={{ margin: "6px 0 4px", fontSize: 13 }}>{item.summary ?? "等待主管下发结构化任务"}</p>
          <span className="bearing">{item.evidence_count ?? 0} 份证据 · {(item.tool_invocations??[]).length} 次工具调用</span>
          {item.failure_reason && <p role="alert">{item.failure_reason} · {item.retryable?"可安全重试":"禁止自动重试"}</p>}
          {item.needs_human_review && <p role="alert">需要人工确认</p>}
        </li>)}
      </ul>
    </section>}

    {reportId && <section className="plate enters">
      <p className="plate-kicker">{t("Report committed")}</p>
      <h2>{t("Decision chain is ready.")}</h2>
      <p style={{ marginTop: 20 }}><a className="button" href={`/reports/${reportId}`}>{t("Read evidence report")}</a></p>
    </section>}

    <section className="plate enters">
      <p className="plate-kicker">{t("SSE channel")}</p>
      <div className="page-head-row">
        <h2>{t("Durable timeline")}</h2>
        <span className="bearing">{run?.current_cursor ?? t("connecting")}</span>
      </div>
      <div style={{ marginTop: 20 }}><RunTimeline events={events} /></div>
    </section>
  </main>;
}
