"use client";

import { useRef, useState } from "react";
import type {
  AgentTeamsRun,
  Clarification,
  Run,
  RunExecutionControl,
} from "../../lib/api-client";
import type { SseEvent } from "../../lib/sse-client";
import { supervisorControlAction, supervisorProgress } from "../../lib/supervisor-experience";
import {
  buildSectorStates,
  advanceEvidenceNotch,
  wheelMotionState,
} from "../../lib/wheel-state";
import { AgentConversationDock } from "./AgentConversationDock";
import { StatusPill } from "../shell/AppShell";
import { useI18n } from "../i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../i18n/LocalizedErrorMessage";
import { EvaluationWheel } from "../workspace/EvaluationWheel";

const RUN_ROLES = [
  { code: "evaluation-manager", label: "Project lead" },
  { code: "user-evidence", label: "Target user" },
  { code: "product-engineering", label: "Product manager" },
  { code: "business-investment", label: "Investor" },
  { code: "evidence-auditor", label: "Evidence calibration" },
] as const;

function toolCategory(code: string): string {
  const value = code.toLowerCase();
  if (/search|browser|web/u.test(value)) return "Public-source research";
  if (/material|document|file|context/u.test(value)) return "Project material review";
  if (/evidence|citation|audit/u.test(value)) return "Evidence calibration";
  return "Structured analysis";
}

export function SupervisorRunExperience({
  run,
  team,
  questions,
  events,
  reportId,
  error,
  projectionError,
  streamDegraded,
  executionControl,
  executionControlBusy = false,
  onPauseExit,
  onResume,
  onRecover,
  onConversationUpdate,
}: {
  run: Run;
  team?: AgentTeamsRun;
  questions: Clarification[];
  events: SseEvent[];
  reportId?: string;
  error?: string;
  projectionError?: string;
  streamDegraded: boolean;
  executionControl?: RunExecutionControl;
  executionControlBusy?: boolean;
  onPauseExit?(): void;
  onResume?(): void;
  onRecover?(): void;
  onConversationUpdate(): void;
}) {
  void events;
  const { t, status } = useI18n();
  const [aperture, setAperture] = useState(false);
  const progress = supervisorProgress(run);
  const stage = run.experience_stage!;
  const needsAttention = run.status === "NEEDS_ATTENTION";
  const failedTasks = (team?.tasks ?? []).filter(item => item.failure_reason);
  const inferredPending = run.dispatch_pending === undefined
    && run.status === "RUNNING"
    && team?.team.binding_status === "PENDING_MANAGER_ACK"
    && team.tasks.some(item => item.stage_code === "LEADER_PLANNING" && item.status === "READY")
    && team.handoff_count === 0
    && team.matrix_event_count === 0;
  const dispatchPending = run.dispatch_pending === true || inferredPending;
  const evidenceTotal = (team?.tasks ?? []).reduce((sum, item) => sum + (item.evidence_count ?? 0), 0);
  const durableNotch = useRef(0);
  const notch = advanceEvidenceNotch(durableNotch.current, evidenceTotal);
  durableNotch.current = notch;
  const motionState = dispatchPending ? "PAUSED" : wheelMotionState(run);
  const headline = dispatchPending
    ? t("Waiting for execution services")
    : run.status === "COMPLETED"
    ? t("The prediction is complete")
    : needsAttention
      ? t("Evaluation needs attention")
    : stage.exception_label
      ? t(stage.exception_label)
      : t(stage.label);
  const controlState = executionControl?.state;
  const controlLabel = controlState === "PAUSED"
    ? t("Paused")
    : controlState === "PAUSE_REQUESTED"
      ? t("Settling {count} submitted calls", { count: executionControl?.in_flight_count ?? 0 })
      : controlState === "PAUSE_BLOCKED"
        ? t("Usage reconciliation required")
        : needsAttention
          ? t("Stopped for review")
        : controlState === "ACTIVE"
          ? t("Evaluation is active")
          : status(run.status);
  const controlAction = supervisorControlAction(run);
  const currentProgress = progress.find(item => item.state === "current") ?? progress.at(-1)!;
  const activelyExecuting = run.status === "RUNNING" && controlState === "ACTIVE";
  const roleRows = RUN_ROLES.map(role => {
    const tasks = (team?.tasks ?? []).filter(item => item.agent_identity_ref.split("@")[0] === role.code);
    const selected = tasks.find(item => ["RUNNING", "LEASED", "WAITING_FOR_USER"].includes(item.status)) ?? tasks.at(-1);
    const categories = Array.from(new Set(tasks.flatMap(item => (
      item.tool_invocations?.length ? item.tool_invocations.map(tool => toolCategory(tool.tool_code)) : item.tool_allowlist.map(toolCategory)
    ))));
    const started = tasks.map(item => item.created_at).filter((value): value is string => Boolean(value)).sort()[0];
    const updated = tasks.map(item => item.updated_at).filter((value): value is string => Boolean(value)).sort().at(-1);
    const elapsedMinutes = started && updated ? Math.max(0, Math.round((Date.parse(updated) - Date.parse(started)) / 60_000)) : null;
    return {
      ...role,
      status: selected?.status ?? "PENDING",
      categories,
      evidence: tasks.reduce((total, item) => total + (item.evidence_count ?? 0), 0),
      elapsedMinutes,
      requests: questions.filter(question => question.agent_code === role.code).length,
    };
  });

  return (
    <main className="workspace-main supervisor-run-page">
      <header className="supervisor-command-bar" data-attention={stage.exception ? "true" : undefined}>
        <div className="supervisor-command-identity">
          <span className="bearing">{t("Prediction target")} {run.project_name ?? t("Current project")}</span>
          <h1>{headline}</h1>
          <p className="supervisor-stage-help">
            {needsAttention
              ? t("Work stopped during {stage}. Completed work and evidence remain preserved.", { stage: t(currentProgress.label) })
              : t(currentProgress.description)}
          </p>
          {stage.exception && (
            <p className="supervisor-command-exception" role="status">
              {stage.exception === "NEEDS_INPUT"
                ? t("Add the missing information in a conversation. Completed work will be kept.")
                : needsAttention
                  ? questions.length
                    ? t("An Agent asked for information. Answer the open question to continue the affected work.")
                    : t("No Agent is waiting for an answer. The run stopped on an execution blocker shown below.")
                  : t("A change needs your confirmation before the prediction can continue.")}
            </p>
          )}
        </div>
        <div className="supervisor-command-state" aria-label={t("Execution status")}>
          <span
            className="status-dot"
            data-state={(needsAttention ? run.status : controlState ?? run.status).toLowerCase()}
            aria-hidden="true"
          />
          <span>{controlLabel}</span>
        </div>
        <div className="supervisor-command-progress">
          <span className="bearing">{t("Four-stage prediction")}</span>
          <ol className="supervisor-progress supervisor-progress-compact">
            {progress.map(item => (
              <li key={item.code} data-state={item.state} aria-current={item.state === "current" ? "step" : undefined}>
                <span className="supervisor-progress-index">0{item.ordinal}</span>
                <span>{t(item.label)}</span>
              </li>
            ))}
          </ol>
        </div>
        <div className="supervisor-command-action">
          {controlAction === "RESUME" ? (
            <button type="button" onClick={onResume} disabled={executionControlBusy}>
              {executionControlBusy ? t("Resuming…") : t("Continue evaluation")}
            </button>
          ) : controlAction === "RECOVER" ? (
            <button type="button" onClick={onRecover} disabled={executionControlBusy}>
              {executionControlBusy ? t("Recovering…") : t("Continue prediction")}
            </button>
          ) : controlAction === "PAUSE" ? (
            <button type="button" className="secondary" onClick={onPauseExit} disabled={executionControlBusy}>
              {executionControlBusy ? t("Requesting pause…") : t("Pause and exit")}
            </button>
          ) : (
            <StatusPill value={run.status} />
          )}
        </div>
      </header>

      <a className="supervisor-return-link" href={`/projects/${run.project_id}/new-evaluation?versionId=${run.product_version_id}&returnRunId=${run.run_id}`}>← {t("Return to product information")}</a>
      {error && <LocalizedErrorMessage value={error} className="error-banner" />}
      {projectionError && <LocalizedErrorMessage value={projectionError} className="error-banner" />}
      {streamDegraded && (
        <p role="status">
          {activelyExecuting
            ? t("Progress syncing is temporarily slower. The saved prediction continues in the background.")
            : t("Progress syncing is temporarily slower. The saved state remains preserved; this does not mean the evaluation is running.")}
        </p>
      )}
      {dispatchPending && (
        <p className="supervisor-inline-notice" role="status">{t("Your project is saved. The prediction will begin as soon as the service is ready.")}</p>
      )}
      {needsAttention && (
        <section className="supervisor-attention-card" aria-labelledby="run-attention-title">
          <div className="supervisor-attention-summary">
            <span className="bearing">{t("What needs attention")}</span>
            <h2 id="run-attention-title">{t("The evaluation stopped before completion")}</h2>
            <LocalizedErrorMessage value={run.attention_reason ?? failedTasks[0]?.failure_reason ?? t("The durable failure reason is not available yet. Refresh the saved status before taking action.")} />
          </div>
          <div className="supervisor-attention-guidance">
            <div>
              <strong>{questions.length ? t("An Agent needs your answer") : t("The four conversations do not contain a question")}</strong>
              <p>
                {questions.length
                  ? t("Open the highlighted conversation and answer the pending question. Completed work remains preserved.")
                  : t("This is an execution or reconciliation blocker, not missing product information, so there is nothing for you to answer there.")}
              </p>
            </div>
            <div>
              <strong>{t("Can this run continue?")}</strong>
              <p>
                {executionControl?.state === "PAUSE_BLOCKED"
                  ? t("This Demo can ignore the previous uncertain state and restart unfinished tasks. Completed results will be kept.")
                  : t("This Demo can restart unfinished tasks from the saved state. Completed results will be kept.")}
              </p>
            </div>
          </div>
          {failedTasks.length > 0 && (
            <details className="supervisor-attention-details">
              <summary>{t("View recorded blocker details")}</summary>
              <ul>
                {failedTasks.map(item => (
                  <li key={item.id}>
                    <strong>{item.agent_identity_ref.split("@")[0].replaceAll("-", " ")}</strong>
                    <LocalizedErrorMessage value={item.failure_reason!} />
                  </li>
                ))}
              </ul>
            </details>
          )}
          <div className="supervisor-attention-actions">
            <button type="button" className="secondary" onClick={onConversationUpdate}>{t("Refresh saved status")}</button>
          </div>
        </section>
      )}

      <section className="plate run-transparency-panel" aria-labelledby="run-transparency-title">
        <header><div><span className="plate-kicker">{t("Transparent execution")}</span><h2 id="run-transparency-title">{t("Who is working and what they may use")}</h2></div><p>{t("Only task status, tool categories, evidence counts, elapsed time, and material requests are shown.")}</p></header>
        <div className="run-transparency-grid">
          {roleRows.map(role => <article key={role.code}>
            <div><strong>{t(role.label)}</strong><StatusPill value={role.status} /></div>
            <dl>
              <div><dt>{t("Tool categories")}</dt><dd>{role.categories.length ? role.categories.map(value => t(value)).join(" · ") : t("Awaiting assignment")}</dd></div>
              <div><dt>{t("Evidence")}</dt><dd>{role.evidence}</dd></div>
              <div><dt>{t("Elapsed time")}</dt><dd>{role.elapsedMinutes === null ? "—" : t("{count} min", { count: role.elapsedMinutes })}</dd></div>
              <div><dt>{t("Material requests")}</dt><dd>{role.requests || t("None")}</dd></div>
            </dl>
          </article>)}
        </div>
      </section>

      <div className="supervisor-stage">
        <div className="supervisor-wheel-column">
          <div className="wheel-frame instrument-shell supervisor-wheel-frame">
            <EvaluationWheel
              sectors={buildSectorStates({})}
              team={team}
              notch={notch}
              motionState={motionState}
              needsAttention={needsAttention}
              showSectorCounts={false}
              architectureGeneration="supervisor-1p4-v1"
              caption={needsAttention ? t("Stopped for review") : t(stage.label)}
            />
            {run.status === "COMPLETED" && reportId ? (
              <button
                type="button"
                className={aperture ? "aperture-button aperture-opening" : "aperture-button"}
                disabled={aperture}
                onClick={() => {
                  setAperture(true);
                  window.setTimeout(() => window.location.assign(`/reports/${reportId}`), 860);
                }}
              >
                <span>{aperture ? t("Opening…") : t("Open wheel")}</span>
                <span className="bearing" style={{ color: "inherit" }}>{t("View project lead report")}</span>
              </button>
            ) : (
              <div className="wheel-core-static pointer-through">
                <span className="core-cta-label">{needsAttention ? status("NEEDS_ATTENTION") : t(stage.label)}</span>
                <span className="core-stage-read">{String(notch).padStart(2, "0")} / 32</span>
              </div>
            )}
          </div>
          <div className="calibration-ring-read" aria-label={t("Prediction status")}>
            <span>{t("Evidence")} <b>{evidenceTotal}</b></span>
            <span data-tone={motionState === "ATTENTION" || motionState === "PAUSED" ? "attention" : undefined}>
              {t("Status")} <b>{status(run.status)}</b>
            </span>
          </div>
        </div>
        {reportId && (
          <a className="button supervisor-stage-report" href={`/reports/${reportId}`}>{t("View prediction result")}</a>
        )}
        <AgentConversationDock
          runId={run.run_id}
          team={team}
          questions={questions}
          onConversationUpdate={onConversationUpdate}
        />
      </div>

    </main>
  );
}
