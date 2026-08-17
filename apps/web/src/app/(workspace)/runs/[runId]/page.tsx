"use client";

import { use, useEffect, useRef, useState } from "react";
import {
  apiBase,
  browserApi,
  reportIdForDisplay,
  sessionFromDocument,
  type AgentTeamsRun,
  type Clarification,
  type Run,
  type RunExecutionControl,
  type UserValidationResult,
} from "../../../../lib/api-client";
import { DurableRunStream, type SseEvent } from "../../../../lib/sse-client";
import { RunTimeline } from "../../../../components/runs/RunTimeline";
import { AgentClarificationConsole } from "../../../../components/runs/AgentClarificationConsole";
import { SupervisorRunExperience } from "../../../../components/runs/SupervisorRunExperience";
import { StatusPill } from "../../../../components/shell/AppShell";
import { useI18n } from "../../../../components/i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../../../../components/i18n/LocalizedErrorMessage";
import { EvaluationWheel } from "../../../../components/workspace/EvaluationWheel";
import {
  buildSectorStates,
  advanceEvidenceNotch,
  stageReadout,
  NOTCHES_PER_REVOLUTION,
  wheelMotionState,
} from "../../../../lib/wheel-state";
import { isSupervisorExperience, supervisorAdmissionEnabled } from "../../../../lib/supervisor-experience";
import { humanizeUserError } from "../../../../lib/user-report-formatter";

export default function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { locale, status, t } = useI18n();
  const { runId } = use(params);
  const [run, setRun] = useState<Run>();
  const [team, setTeam] = useState<AgentTeamsRun>();
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [error, setError] = useState<string>();
  const [projectionError, setProjectionError] = useState<string>();
  const [streamDegraded, setStreamDegraded] = useState(false);
  const [reportId, setReportId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [replay, setReplay] = useState(0);
  const [questions, setQuestions] = useState<Clarification[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [aperture, setAperture] = useState(false);
  const [userValidation, setUserValidation] = useState<UserValidationResult>();
  const [summaryReportHtml, setSummaryReportHtml] = useState<string>();
  const [summaryReportError, setSummaryReportError] = useState<string>();
  const [recheckFile, setRecheckFile] = useState<File>();
  const [recheckSource, setRecheckSource] = useState("");
  const [recheckObservedAt, setRecheckObservedAt] = useState("");
  const [recheckSampleSize, setRecheckSampleSize] = useState("");
  const [recheckSegment, setRecheckSegment] = useState("");
  const [recheckObservation, setRecheckObservation] = useState("");
  const [recheckBusy, setRecheckBusy] = useState(false);
  const [controlBusy, setControlBusy] = useState(false);
  const [developerMode, setDeveloperMode] = useState(false);
  const durableNotch = useRef(0);
  const automaticDispatchRun = useRef<string | undefined>(undefined);
  const hasUserValidationRuntime = team?.tasks.some(task =>
    task.tool_invocations?.some(invocation => invocation.tool_code.startsWith("user-validation-designer.")),
  ) ?? false;

  useEffect(() => {
    setDeveloperMode(new URLSearchParams(window.location.search).get("debug") === "1");
  }, []);

  // ADR 0004: keep polling while the Run is parked on a question, otherwise the
  // quest prompt would never appear once the status leaves RUNNING.
  useEffect(() => { let stream: DurableRunStream | undefined; try { const session = sessionFromDocument(); browserApi().getRun(runId).then(snapshot => { setProjectionError(undefined); setRun(snapshot); if(snapshot.status === "COMPLETED") void browserApi().getReportForRunDisplay(runId).then(report=>setReportId(reportIdForDisplay(report))); }).catch(cause => setProjectionError(cause.message)); const query=replay > 0 ? "?cursor=event.initial" : ""; stream = new DurableRunStream(`${apiBase()}/api/v1/runs/${runId}/events${query}`, { "X-Tenant-Id": session.tenantId, "X-Actor-Id": session.actorId, "X-Correlation-Id": crypto.randomUUID() }, { onSnapshot: snapshot => { setRun(snapshot as Run); setProjectionError(undefined); setStreamDegraded(false); }, onEvent: event => setEvents(current => current.some(item => item.id === event.id) ? current : [...current, event]), onError: () => setStreamDegraded(true), onClosed: () => setStreamDegraded(true) }, undefined, async () => browserApi().getRun(runId) as unknown as Record<string, unknown>); void stream.connect(); } catch (cause) { setError(cause instanceof Error ? cause.message : t("No workspace session")); } return () => stream?.stop(); }, [runId,replay,t]);
  useEffect(()=>{if(!streamDegraded)return; const refresh=()=>void browserApi().getRun(runId).then(snapshot=>{setRun(snapshot);setProjectionError(undefined);}).catch(cause=>setProjectionError(cause instanceof Error?cause.message:t("Run status refresh failed"))); refresh(); const timer=window.setInterval(refresh,3000); return()=>window.clearInterval(timer);},[streamDegraded,runId,t]);
  useEffect(()=>{if(run?.status==="COMPLETED"&&!reportId){void browserApi().getReportForRunDisplay(runId).then(report=>setReportId(reportIdForDisplay(report))).catch(cause=>setError(cause instanceof Error?cause.message:t("Report loading failed")));}},[run?.status,reportId,runId,t]);
  useEffect(()=>{if(!run||run.status==="PLANNED")return; const refresh=()=>void browserApi().getAgentTeamsRun(runId).then(value=>{setTeam(value);setProjectionError(undefined);}).catch(cause=>setProjectionError(cause instanceof Error?cause.message:t("AgentTeams projection failed"))); refresh(); if(run.status!=="RUNNING")return; const timer=window.setInterval(refresh,3000); return()=>window.clearInterval(timer);},[run?.status,runId,t]);
  useEffect(()=>{if(!run||run.status==="PLANNED"||run.status==="COMPLETED")return; const refresh=()=>void browserApi().listClarifications(runId).then(result=>setQuestions(result.items)).catch(()=>undefined); refresh(); if(run.status!=="RUNNING"&&run.status!=="WAITING_FOR_USER")return; const timer=window.setInterval(refresh,3000); return()=>window.clearInterval(timer);},[run?.status,runId]);
  useEffect(() => {
    if (!run || run.status === "PLANNED" || !hasUserValidationRuntime) return;
    const refresh = () => void browserApi().getUserValidationResult(runId).then(setUserValidation).catch(() => undefined);
    refresh();
    if (run.status !== "RUNNING") return;
    const timer = window.setInterval(refresh, 3000);
    return () => window.clearInterval(timer);
  }, [hasUserValidationRuntime, run?.status, runId]);
  useEffect(() => {
    let cancelled = false;
    if (!userValidation?.presentation?.summary.html.available) {
      setSummaryReportHtml(undefined);
      setSummaryReportError(undefined);
      return;
    }
    void browserApi().getUserValidationReport(runId, "summary", "html").then(value => {
      if (!cancelled) {
        setSummaryReportHtml(value.content);
        setSummaryReportError(undefined);
      }
    }).catch(cause => {
      if (!cancelled) {
        setSummaryReportHtml(undefined);
        setSummaryReportError(cause instanceof Error ? cause.message : t("Summary report loading failed"));
      }
    });
    return () => { cancelled = true; };
  }, [runId, userValidation?.skill_result_ref, userValidation?.presentation?.summary.html.available]);

  useEffect(() => {
    if (!run || run.status !== "PLANNED" || run.execution_control?.state !== "ACTIVE") return;
    if (!supervisorAdmissionEnabled()) return;
    if (automaticDispatchRun.current === run.run_id) return;
    automaticDispatchRun.current = run.run_id;
    void execute(`automatic-dispatch:${run.run_id}`);
  }, [run?.execution_control?.state, run?.run_id, run?.status, run?.ui_mode]);

  useEffect(() => {
    if (run?.status !== "RUNNING" || run.execution_control?.state !== "ACTIVE") return;
    const warnActiveEvaluation = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = t("This evaluation will continue unless you use Pause and exit.");
    };
    window.addEventListener("beforeunload", warnActiveEvaluation);
    return () => window.removeEventListener("beforeunload", warnActiveEvaluation);
  }, [run?.execution_control?.state, run?.status, t]);

  useEffect(() => {
    if (run?.execution_control?.state !== "PAUSE_REQUESTED") return;
    const refresh = () => void browserApi().getRunExecutionControl(runId).then(control => {
      setRun(current => current ? { ...current, execution_control: control } : current);
      if (control.state === "PAUSED" && run) window.location.assign(`/projects/${run.project_id}`);
    }).catch(cause => setProjectionError(cause instanceof Error ? cause.message : t("Pause settlement refresh failed")));
    refresh();
    const timer = window.setInterval(refresh, 1500);
    return () => window.clearInterval(timer);
  }, [run?.execution_control?.state, run?.project_id, runId, t]);

  useEffect(() => {
    const state = run?.execution_control?.state;
    if (!state || state === "ACTIVE" || state === "CLOSED" || state === "PAUSE_REQUESTED") return;
    void browserApi().getRunExecutionControl(runId).then(control => {
      setRun(current => current ? { ...current, execution_control: control } : current);
    }).catch(cause => setProjectionError(cause instanceof Error ? cause.message : t("Execution control refresh failed")));
  }, [run?.execution_control?.state, runId, t]);

  async function pauseAndExit() {
    if (!run?.execution_control || !window.confirm(t("Pause this evaluation after all currently submitted calls settle?"))) return;
    setControlBusy(true);
    setError(undefined);
    try {
      const control = await browserApi().pauseRun(
        runId,
        run.execution_control.control_epoch,
        `pause:${runId}:${run.execution_control.control_epoch}`,
      );
      setRun(current => current ? { ...current, execution_control: control } : current);
      if (control.state === "PAUSED") window.location.assign(`/projects/${run.project_id}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Pause failed"));
    } finally {
      setControlBusy(false);
    }
  }

  async function resumeEvaluation() {
    if (!run?.execution_control) return;
    setControlBusy(true);
    setError(undefined);
    try {
      const control = await browserApi().resumeRun(
        runId,
        run.execution_control.control_epoch,
        `resume:${runId}:${run.execution_control.control_epoch}`,
      );
      setRun(current => current ? { ...current, execution_control: control } : current);
      setReplay(value => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Resume failed"));
    } finally {
      setControlBusy(false);
    }
  }

  async function recoverEvaluation() {
    if (
      !run?.execution_control
      || !window.confirm(t("Restart unfinished tasks? Completed results will be kept."))
    ) return;
    setControlBusy(true);
    setError(undefined);
    try {
      const result = await browserApi().recoverRun(
        runId,
        run.execution_control.control_epoch,
        `recover:${runId}:${run.execution_control.control_epoch}`,
      );
      const [nextRun, nextTeam, nextQuestions] = await Promise.all([
        browserApi().getRun(runId),
        browserApi().getAgentTeamsRun(runId).catch(() => undefined),
        browserApi().listClarifications(runId).catch(() => ({ items: [] as Clarification[] })),
      ]);
      setRun({ ...nextRun, status: result.run_status, execution_control: result.execution_control });
      if (nextTeam) setTeam(nextTeam);
      setQuestions(nextQuestions.items);
      setProjectionError(undefined);
      setReplay(value => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Recovery failed"));
    } finally {
      setControlBusy(false);
    }
  }

  function executionControlPanel(control?: RunExecutionControl) {
    if (!control || control.state === "CLOSED" || ["COMPLETED", "FAILED", "CANCELLED", "EXPIRED"].includes(run?.status ?? "")) return null;
    const paused = control.state === "PAUSED";
    const settling = control.state === "PAUSE_REQUESTED";
    const blocked = control.state === "PAUSE_BLOCKED";
    return (
      <section className="plate run-execution-control" aria-live="polite">
        <div className="page-head-row">
          <div>
            <p className="plate-kicker">{t("Prediction control")}</p>
            <h2>
              {paused
                ? t("Paused · no new evaluation Token will be used")
                : settling
                  ? t("Settling {count} submitted calls", { count: control.in_flight_count })
                  : blocked
                    ? t("Pause requires usage reconciliation")
                    : t("Evaluation is active")}
            </h2>
          </div>
        </div>
        <p>
          {settling
            ? t("The {count} already submitted calls may still add Token usage. Every later model and tool call is blocked locally.", {
                count: control.in_flight_count,
              })
              : paused
              ? t("Reopening this page does not resume the evaluation. Completed tasks, evidence, and budget remain preserved.")
              : blocked
                ? control.last_error
                  ? humanizeUserError(control.last_error, locale)
                  : t("Submission or billing state is unknown. Automatic retry is prohibited.")
                : t("Leaving this browser page alone does not pause the evaluation.")}
        </p>
        {control.checkpoint && (
          <p className="bearing">
            {t("Completed {completed} tasks · {evidence} evidence items", {
              completed: control.checkpoint.completed_task_ids.length,
              evidence: control.checkpoint.evidence_ids.length,
            })}
          </p>
        )}
        {control.remaining_budget && (
          <p className="bearing">
            {t("Remaining budget: {amount} {currency}", {
              amount: control.remaining_budget.remaining,
              currency: control.remaining_budget.currency,
            })}
          </p>
        )}
        <div className="form-actions">
          {paused ? (
            <button type="button" onClick={() => void resumeEvaluation()} disabled={controlBusy}>
              {controlBusy ? t("Resuming…") : t("Continue evaluation")}
            </button>
          ) : control.state === "ACTIVE" ? (
            <button type="button" className="button secondary" onClick={() => void pauseAndExit()} disabled={controlBusy}>
              {controlBusy ? t("Requesting pause…") : t("Pause and exit")}
            </button>
          ) : null}
        </div>
      </section>
    );
  }

  async function execute(idempotencyKey = `manual-dispatch:${runId}`) {
    setBusy(true);
    setError(undefined);
    try {
      const result = await browserApi().dispatch(runId, idempotencyKey);
      setRun(current => current ? { ...current, status: result.status, current_stage: "LEADER_PLANNING" } : current);
      setReplay(value => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Dispatch failed"));
    } finally {
      setBusy(false);
    }
  }

  function openReport() {
    if (!reportId) return;
    setAperture(true);
    window.setTimeout(() => window.location.assign(`/reports/${reportId}`), 860);
  }

  async function createEvidenceRecheck() {
    if (!run) return;
    setRecheckBusy(true);
    setError(undefined);
    try {
      if (!recheckFile || !recheckSource.trim() || !recheckObservedAt || !recheckObservation.trim()) {
        throw new Error(t("Evidence recheck requires a file, traceable source, observation time, and aggregate observation."));
      }
      const api = browserApi();
      const uploaded = await api.uploadMaterial(run.product_version_id, recheckFile);
      await api.registerUserEvidence(run.product_version_id, {
        object_key: uploaded.object_key,
        sha256: uploaded.sha256,
        kind: "interview",
        claimed_tier: "E3",
        source: recheckSource.trim(),
        observed_at: new Date(recheckObservedAt).toISOString(),
        expires_at: null,
        sample_size: recheckSampleSize ? Number(recheckSampleSize) : null,
        segment: recheckSegment.trim() || null,
        aggregate_observation: recheckObservation.trim(),
        applicability: {},
        supporting_claim_refs: [],
        contradicting_claim_refs: [],
      });
      const recheck = await api.createUserEvidenceRecheck(runId);
      window.location.assign(`/runs/${recheck.run_id}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Evidence recheck creation failed"));
    } finally {
      setRecheckBusy(false);
    }
  }

  async function downloadUserValidationReport(
    variant: "summary" | "full",
    format: "html" | "markdown",
  ) {
    try {
      const report = await browserApi().getUserValidationReport(runId, variant, format);
      const blob = new Blob([report.content], {
        type: format === "html" ? "text/html;charset=utf-8" : "text/markdown;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `user-validation-${variant}.${format === "html" ? "html" : "md"}`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("User-validation report download failed"));
    }
  }

  const evidenceTotal = (team?.tasks ?? []).reduce((sum, task) => sum + (task.evidence_count ?? 0), 0);
  const notch = advanceEvidenceNotch(durableNotch.current, evidenceTotal);
  durableNotch.current = notch;
  const completed = run?.status === "COMPLETED";
  const needsAttention = run?.status === "NEEDS_ATTENTION";
  const waitingForUser = run?.status === "WAITING_FOR_USER";
  const headline = completed
    ? t("Evaluation completed")
    : needsAttention
      ? t("Evaluation paused and needs your attention")
      : waitingForUser
        ? t("A few questions need confirmation")
        : t("Evaluation in progress");
  const deck = completed
    ? t("Six Agents have completed the review. Open the full verdict and supporting evidence below.")
    : needsAttention
      ? t("The system encountered an uncertain condition, stopped, and preserved the state without automatic retry. Expand the tasks below for the durable reason.")
      : waitingForUser
        ? t("An Agent needs more information to continue. Only affected work will rerun; completed work remains intact.")
        : t("Agents are investigating. The instrument advances once per committed evidence item—and stays still without evidence.");

  if (run && isSupervisorExperience(run)) {
    const refreshConversationState = () => void Promise.all([
      browserApi().getRun(runId),
      browserApi().getAgentTeamsRun(runId).catch(() => undefined),
      browserApi().listClarifications(runId).catch(() => ({ items: [] as Clarification[] })),
    ]).then(([nextRun, nextTeam, nextQuestions]) => {
      setRun(nextRun);
      if (nextTeam) setTeam(nextTeam);
      setQuestions(nextQuestions.items);
      setProjectionError(undefined);
    }).catch(cause => {
      setProjectionError(cause instanceof Error ? cause.message : t("Durable state refresh failed"));
    });
    return (
      <SupervisorRunExperience
        run={run}
        team={team}
        questions={questions}
        events={events}
        reportId={reportId}
        error={error}
        projectionError={projectionError}
        streamDegraded={streamDegraded}
        executionControl={run.execution_control}
        executionControlBusy={controlBusy}
        onPauseExit={() => void pauseAndExit()}
        onResume={() => void resumeEvaluation()}
        onRecover={() => void recoverEvaluation()}
        onConversationUpdate={refreshConversationState}
      />
    );
  }

  return (
    <main className="workspace-main workspace-main-tall">
      <div className="page-head page-head-compact">
        <div className="page-head-row">
          <div>
            <span className="bearing">{t("Prediction target")} {run?.project_name ?? t("Current project")}</span>
            <h1>{headline}</h1>
            <p>{deck}</p>
          </div>
          {run && <StatusPill value={run.status} />}
        </div>
      </div>
      {run && <a className="supervisor-return-link" href={`/projects/${run.project_id}/new-evaluation?versionId=${run.product_version_id}&returnRunId=${runId}`}>← {t("Return to product information")}</a>}
      {error && <LocalizedErrorMessage value={error} />}
      {projectionError && <LocalizedErrorMessage value={projectionError} />}
      {streamDegraded && <p role="status">{t("Progress syncing is temporarily slower. The saved prediction continues in the background.")}</p>}
      {executionControlPanel(run?.execution_control)}

      <div className="run-stage" data-drawer-open={drawerOpen || undefined}>
        <div className="wheel-pane">
          <div className="wheel-frame instrument-shell">
            <EvaluationWheel
              sectors={buildSectorStates({})}
              team={team}
              notch={notch}
              motionState={wheelMotionState(run)}
              needsAttention={needsAttention}
              showSectorCounts={false}
            />
            {run?.status === "PLANNED" ? (
              <div className="wheel-core-static pointer-through">
                <span className="core-cta-label">{t("Historical plan · read only")}</span>
                <span className="core-stage-read">{status(stageReadout(run))}</span>
              </div>
            ) : completed ? (
              <button
                className={aperture ? "aperture-button aperture-opening" : "aperture-button"}
                onClick={openReport}
                disabled={!reportId || aperture}
              >
                <span>{aperture ? t("Opening…") : t("View verdict")}</span>
                <span className="bearing" style={{ color: "inherit" }}>{t("Report")}</span>
              </button>
            ) : (
              <div className="wheel-core-static pointer-through">
                <span className="core-cta-label">{needsAttention ? status("NEEDS_ATTENTION") : status(stageReadout(run))}</span>
                <span className="core-stage-read">
                  {String(notch).padStart(2, "0")} / {NOTCHES_PER_REVOLUTION}
                </span>
              </div>
            )}
          </div>

          <div className="calibration-ring-read" aria-label={t("Evidence calibration status")}>
            <span data-tone={notch >= NOTCHES_PER_REVOLUTION ? "ok" : undefined}>
              {t("Evidence")} <b>{evidenceTotal}</b>
            </span>
            <span data-tone={waitingForUser || needsAttention ? "attention" : undefined}>
              {t("Status")} <b>{needsAttention ? status("NEEDS_ATTENTION") : status(stageReadout(run))}</b>
            </span>
            {developerMode && <span>
              {t("Budget")} <b>
                {team?.budget
                  ? `$${Number(team.budget.consumed).toFixed(2)} / $${Number(team.budget.limit).toFixed(0)}`
                  : t("$20 cap")}
              </b>
            </span>}
          </div>
        </div>

        <AgentClarificationConsole
          team={team}
          questions={questions}
          runStatus={run?.status}
          onDrawerChange={setDrawerOpen}
          onAnswer={async (answers, idempotencyKey) => {
            const result = await browserApi().answerClarifications(runId, answers, idempotencyKey);
            setRun(current => current ? { ...current, status: result.run_status } : current);
            const [remaining, projection] = await Promise.all([
              browserApi().listClarifications(runId).catch(() => ({ items: [] as Clarification[] })),
              browserApi().getAgentTeamsRun(runId).catch(() => undefined),
            ]);
            setQuestions(remaining.items);
            if (projection) setTeam(projection);
            return result;
          }}
        />
      </div>

      {run?.status === "PLANNED" && (
        <section className="plate enters">
          <p className="plate-kicker">{t("Historical generation")}</p>
          <h2>{t("Submit a new version and re-evaluate")}</h2>
          <p>{t("This historical planned evaluation cannot be dispatched. Create a new 1+4 evaluation; the old record remains readable.")}</p>
          <div className="form-actions">
            <a className="button" href={`/projects/${run.project_id}/new-evaluation?versionId=${run.product_version_id}&returnRunId=${runId}`}>
              {t("Create a new 1+4 evaluation")}
            </a>
          </div>
        </section>
      )}

      {developerMode && userValidation && (
        <section className="plate enters" aria-label={t("User-validation report")}>
          <p className="plate-kicker">{t("User research report")}</p>
          <h2>{t("The user report entered the evidence-calibration chain")}</h2>
          <div className="grid-auto" style={{ marginTop: 18 }}>
            <dl className="readout"><dt>{t("Skill result")}</dt><dd>{userValidation.status}</dd></dl>
            <dl className="readout">
              <dt>{t("Conclusion boundary")}</dt>
              <dd>{userValidation.summary.preliminary ? t("Preliminary · awaiting or missing applicable E3+") : t("Applicable real evidence exists")}</dd>
            </dl>
          </div>
          <p style={{ marginTop: 16 }}>
            {String(userValidation.summary.result_summary ?? t("Report completed. The user role does not issue a project-level final recommendation."))}
          </p>
          {summaryReportHtml && (
            <iframe
              title={t("User-validation summary report")}
              sandbox=""
              referrerPolicy="no-referrer"
              srcDoc={summaryReportHtml}
              style={{ width: "100%", minHeight: 720, marginTop: 20, border: "1px solid var(--line)", borderRadius: 18, background: "white" }}
            />
          )}
          {summaryReportError && <LocalizedErrorMessage value={summaryReportError} className="error-banner" />}
          <div className="form-actions" style={{ marginTop: 18, flexWrap: "wrap" }}>
            {userValidation.presentation && (
              <>
                <a className="button" href={`/runs/${runId}/user-validation-report`} target="_blank" rel="noreferrer">
                  {t("Open complete report separately")}
                </a>
                {(["summary", "full"] as const).flatMap(variant =>
                  (["html", "markdown"] as const).map(format => (
                    <button
                      key={`${variant}-${format}`}
                      type="button"
                      className="button secondary"
                      onClick={() => void downloadUserValidationReport(variant, format)}
                    >
                      {t("Download {variant}{format}", { variant: variant === "summary" ? t("summary") : t("complete"), format: format === "html" ? " HTML" : " Markdown" })}
                    </button>
                  )),
                )}
              </>
            )}
            <a className="button secondary" href={userValidation.report_url} target="_blank" rel="noreferrer">
              {t("Open raw machine JSON")}
            </a>
          </div>

          {completed && (
            <details className="intake-group" style={{ marginTop: 22 }}>
              <summary>
                <span>{t("Add real evidence and create an append-only recheck Run")}</span>
                <span className="g-meta">{t("This report will not be overwritten")}</span>
              </summary>
              <div className="field-set">
                <label>
                  <span className="field-name">{t("Evidence file without PII")}</span>
                  <input
                    type="file"
                    accept=".json,.csv,.txt,application/json,text/csv,text/plain"
                    onChange={event => setRecheckFile(event.target.files?.[0])}
                  />
                </label>
                <label>
                  <span className="field-name">{t("Traceable source")}</span>
                  <input value={recheckSource} onChange={event => setRecheckSource(event.target.value)} />
                </label>
                <label>
                  <span className="field-name">{t("Observed at")}</span>
                  <input
                    type="datetime-local"
                    value={recheckObservedAt}
                    onChange={event => setRecheckObservedAt(event.target.value)}
                  />
                </label>
                <label>
                  <span className="field-name">{t("Sample size")}</span>
                  <input
                    type="number"
                    min={1}
                    value={recheckSampleSize}
                    onChange={event => setRecheckSampleSize(event.target.value)}
                  />
                </label>
                <label>
                  <span className="field-name">{t("Applicable segment")}</span>
                  <input value={recheckSegment} onChange={event => setRecheckSegment(event.target.value)} />
                </label>
                <label>
                  <span className="field-name">{t("Aggregate observation")}</span>
                  <textarea
                    value={recheckObservation}
                    onChange={event => setRecheckObservation(event.target.value)}
                    placeholder={t("e.g. 9 of 12 target users completed the task; 7 returned one week later. Do not include identifying information.")}
                  />
                </label>
              </div>
              <div className="form-actions">
                <button onClick={createEvidenceRecheck} disabled={recheckBusy}>
                  {recheckBusy ? t("Creating recheck…") : t("Register as E3 interview evidence and create recheck Run")}
                </button>
              </div>
            </details>
          )}
        </section>
      )}

      {reportId && (
        <p className="run-report-cta">
          <a className="button" href={`/reports/${reportId}`}>{t("Read evidence report")}</a>
        </p>
      )}

      {developerMode && <details className="run-details">
        <summary>{t("Run details · tasks / timeline / Manifest / technical readings")}</summary>
        <div className="grid-auto" style={{ marginTop: 18 }}>
          <dl className="readout"><dt>{t("Current stage")}</dt><dd>{run?.current_stage ? status(run.current_stage) : t("Awaiting")}</dd></dl>
          <dl className="readout"><dt>{t("Events received")}</dt><dd>{events.length}</dd></dl>
          <dl className="readout"><dt>{t("Standard")}</dt><dd>{run?.standard_version ?? "—"}</dd></dl>
          <dl className="readout"><dt>{t("Cursor")}</dt><dd>{run?.current_cursor === "event.initial" ? t("Initial") : t("Durable")}</dd></dl>
        </div>
        {team ? (
          <ul className="record-list" style={{ marginTop: 18 }}>
            {team.tasks.map(item => (
              <li key={item.id} style={{ display: "block" }}>
                <div className="page-head-row" style={{ alignItems: "baseline" }}>
                  <span>
                    <span className="bearing">{item.stage_code.replaceAll("_", " ")}</span>
                    <strong style={{ fontSize: 16 }}>{item.agent_identity_ref.split("@")[0].replaceAll("-", " ")}</strong>
                  </span>
                  <StatusPill value={item.status} />
                </div>
                <p style={{ margin: "6px 0 4px", fontSize: 13 }}>{item.summary ?? t("Awaiting a structured task from the project lead")}</p>
                <span className="bearing">{t("{evidence} evidence items · {tools} tool calls", { evidence: item.evidence_count ?? 0, tools: (item.tool_invocations ?? []).length })}</span>
                {item.failure_reason && <p role="alert">{humanizeUserError(item.failure_reason, locale)} · {item.retryable ? t("Safe to retry") : t("Automatic retry prohibited")}</p>}
                {item.needs_human_review && <p role="alert">{t("Human confirmation required")}</p>}
              </li>
            ))}
          </ul>
        ) : (
          <p style={{ marginTop: 18, fontSize: 13 }}>{t("The task list is projected by AgentTeams after dispatch. This Run is not dispatched, so no task facts are available.")}</p>
        )}
        <div style={{ marginTop: 22 }}>
          <p className="plate-kicker">{t("SSE channel")} · {run?.current_cursor ?? t("connecting")}</p>
          <RunTimeline events={events} />
        </div>
      </details>}
    </main>
  );
}
