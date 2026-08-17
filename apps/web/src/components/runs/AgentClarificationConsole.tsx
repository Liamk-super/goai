"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentTeamsRun, Clarification, ClarificationAnswerResult } from "../../lib/api-client";
import { buildAgentTabs, type AgentTab } from "../../lib/agent-tabs";
import { AGENT_GLYPHS } from "../../lib/agent-glyphs";
import { useI18n } from "../i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../i18n/LocalizedErrorMessage";
import { StatusPill } from "../shell/AppShell";

function AgentGlyph({ code }: { code: string }) {
  return (
    <svg className="tab-glyph" viewBox="-13 -13 26 26" aria-hidden="true">
      <path d={AGENT_GLYPHS[code] ?? AGENT_GLYPHS.default} fill="currentColor" />
    </svg>
  );
}

export function AgentClarificationConsole({
  team,
  questions,
  runStatus,
  onAnswer,
  onDrawerChange,
}: {
  team: AgentTeamsRun | undefined;
  questions: Clarification[];
  runStatus?: string;
  onAnswer(
    answers: { request_id: string; answer: string }[],
    idempotencyKey: string,
  ): Promise<ClarificationAnswerResult>;
  onDrawerChange?(open: boolean): void;
}) {
  const { t } = useI18n();
  const tabs = useMemo(() => buildAgentTabs(team, questions), [team, questions]);
  const [activeCode, setActiveCode] = useState<string>();
  const [open, setOpen] = useState(false);
  const [toastDismissed, setToastDismissed] = useState(false);
  const submissionKey = useRef(crypto.randomUUID());
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [resumed, setResumed] = useState<ClarificationAnswerResult>();

  // A blocked Agent claims focus first, but the tablist must always keep one
  // selected tab: with none selected every tab is tabIndex=-1 and the list has
  // no keyboard entry point at all.
  const preferredCode = (tabs.find(tab => tab.pending.length > 0) ?? tabs[0])?.code;

  useEffect(() => {
    if (!activeCode && preferredCode) setActiveCode(preferredCode);
  }, [activeCode, preferredCode]);

  useEffect(() => {
    onDrawerChange?.(open);
  }, [open, onDrawerChange]);

  const blockedTab = tabs.find(tab => tab.pending.length > 0);
  const showToast = Boolean(blockedTab) && !open && !toastDismissed;
  const active = tabs.find(tab => tab.code === activeCode);

  function openAgent(code: string) {
    setActiveCode(code);
    setOpen(true);
    setToastDismissed(true);
  }

  async function submit(pending: Clarification[]) {
    setBusy(true);
    setError(undefined);
    try {
      const payload = pending.map(item => ({ request_id: item.request_id, answer: (drafts[item.request_id] ?? "").trim() }));
      if (payload.some(item => !item.answer)) throw new Error(t("Every open question needs an answer."));
      // Retrying after a timeout must replay the same key, so it only rotates
      // once the server has actually committed this submission.
      const result = await onAnswer(payload, submissionKey.current);
      submissionKey.current = crypto.randomUUID();
      setResumed(result);
      setToastDismissed(false);
      setDrafts(current => {
        const next = { ...current };
        for (const item of pending) delete next[item.request_id];
        return next;
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Answer submission failed"));
    } finally {
      setBusy(false);
    }
  }

  const activeTask = active
    ? team?.tasks.find(task => task.agent_identity_ref.split("@")[0] === active.code && task.summary)
      ?? team?.tasks.find(task => task.agent_identity_ref.split("@")[0] === active.code)
    : undefined;

  return (
    <>
      {showToast && blockedTab && (
        <button
          type="button"
          className="quest-toast"
          onClick={() => openAgent(blockedTab.code)}
          aria-label={t("{count} open question(s)", { count: blockedTab.pending.length })}
        >
          <AgentGlyph code={blockedTab.code} />
          <span>
            <strong>{t("{count} information items need your input", { count: blockedTab.pending.length })}</strong>
            <span>{t("{name} · select to review and answer", { name: t(blockedTab.name) })}</span>
          </span>
        </button>
      )}

      <aside className="agent-rail" data-open={open || undefined} aria-label={t("Specialist agents")}>
        <div className="agent-edge-tabs" role="tablist" aria-label={t("Specialist agents")} aria-orientation="vertical">
          {tabs.map((tab, index) => {
            const blocked = tab.pending.length > 0;
            const selected = tab.code === activeCode;
            return (
              <button
                key={tab.code}
                id={`agent-tab-${tab.code}`}
                role="tab"
                type="button"
                className="agent-edge-tab"
                aria-selected={selected}
                aria-controls={`agent-panel-${tab.code}`}
                tabIndex={selected ? 0 : -1}
                data-blocked={blocked ? "true" : undefined}
                data-state={tab.status.toLowerCase()}
                onClick={() => openAgent(tab.code)}
                onKeyDown={event => {
                  const last = tabs.length - 1;
                  const target =
                    event.key === "ArrowDown" || event.key === "ArrowRight" ? (index === last ? 0 : index + 1)
                    : event.key === "ArrowUp" || event.key === "ArrowLeft" ? (index === 0 ? last : index - 1)
                    : event.key === "Home" ? 0
                    : event.key === "End" ? last
                    : undefined;
                  if (target === undefined) return;
                  event.preventDefault();
                  const next = tabs[target];
                  setActiveCode(next.code);
                  document.getElementById(`agent-tab-${next.code}`)?.focus();
                }}
              >
                <AgentGlyph code={tab.code} />
                <span>
                  <span className="tab-name">{tab.name}</span>
                  <span className="tab-read">
                    {blocked ? t("needs your answer") : tab.evidence ? t(tab.evidence === 1 ? "1 evidence item" : "{count} evidence items", { count: tab.evidence }) : tab.status.replaceAll("_", " ").toLowerCase()}
                  </span>
                </span>
                {blocked && (
                  <span className="quest-badge" aria-label={t("{count} open question(s)", { count: tab.pending.length })}>
                    {tab.pending.length}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {open && active && (
          <div
            id={`agent-panel-${active.code}`}
            role="tabpanel"
            aria-labelledby={`agent-tab-${active.code}`}
            tabIndex={0}
            className="agent-drawer agent-panel"
          >
          <div className="drawer-head">
            <div>
              <span className="bearing">{active.code.replaceAll("-", " ")}</span>
              <h3>{active.name}</h3>
              <span className="bearing">
                {t(active.evidence === 1 ? "1 evidence item" : "{count} evidence items", { count: active.evidence })}
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <StatusPill value={active.status} />
              <button type="button" className="quiet" onClick={() => setOpen(false)} aria-label={t("Collapse Agent drawer")}>
                {t("Collapse")}
              </button>
            </div>
          </div>

          {activeTask?.summary && <p style={{ fontSize: 13 }}>{activeTask.summary}</p>}

          {active.pending.length === 0 ? (
            <div className="empty-state">
              <strong>{t("No open questions from this Agent.")}</strong>
              <p>{t("It will raise a prompt here only when a missing product fact blocks its conclusion.")}</p>
            </div>
          ) : (
            <form
              onSubmit={event => {
                event.preventDefault();
                void submit(active.pending);
              }}
            >
              <p>{t("Your answer updates the project information. Only the affected analysis will run again.")}</p>
              {active.pending.map(item => (
                <label key={item.request_id}>
                  <span className="field-name">{item.question}</span>
                  <span className="field-hint">
                    {t("Why this is needed")}: {item.why_blocking}
                  </span>
                  <textarea
                    required
                    maxLength={4000}
                    value={drafts[item.request_id] ?? ""}
                    onChange={event => setDrafts({ ...drafts, [item.request_id]: event.target.value })}
                  />
                </label>
              ))}
              {error && <LocalizedErrorMessage value={error} />}
              <div className="form-actions">
                <button disabled={busy}>{busy ? t("Committing answer…") : t("Answer and resume")}</button>
              </div>
            </form>
          )}

          {resumed && (
            <dl className="readout" role="status" aria-live="polite">
              <dt>{t("Resumed")}</dt>
              <dd>
                {t("{affected} task(s) re-dispatched · {kept} kept", {
                  affected: resumed.affected_task_ids.length,
                  kept: resumed.unaffected_task_ids.length,
                })}{" "}
                · {resumed.run_status}
              </dd>
            </dl>
          )}
          </div>
        )}
      </aside>

      {runStatus && questions.length > 0 && !open && (
        <span className="bearing" role="status" aria-live="polite" style={{ position: "absolute", left: -9999 }}>
          {t("Waiting for you · {count} open", { count: questions.length })}
        </span>
      )}
    </>
  );
}

export type { AgentTab };
