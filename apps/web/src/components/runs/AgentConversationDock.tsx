"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  browserApi,
  type AgentTeamsRun,
  type Clarification,
  type ConversationChannel,
  type ConversationChannelState,
  type ConversationMessage,
} from "../../lib/api-client";
import { AGENT_GLYPHS } from "../../lib/agent-glyphs";
import { RUN_CONVERSATION_CHANNELS } from "../../lib/run-conversations";
import { useI18n } from "../i18n/LocaleProvider";
import { StatusPill } from "../shell/AppShell";
import { formatUserVisibleAgentText } from "../../lib/user-report-formatter";
import { LocalizedErrorMessage } from "../i18n/LocalizedErrorMessage";
import { translateGapQuestion } from "../../lib/i18n";

function ChannelGlyph({ channel }: { channel: ConversationChannel }) {
  return (
    <svg className="conversation-tab-glyph" viewBox="-13 -13 26 26" aria-hidden="true">
      <path d={AGENT_GLYPHS[channel] ?? AGENT_GLYPHS.default} fill="currentColor" />
    </svg>
  );
}

function fallbackStates(team: AgentTeamsRun | undefined, questions: Clarification[]): ConversationChannelState[] {
  return RUN_CONVERSATION_CHANNELS.map(({ channel }) => {
    const tasks = channel === "supervisor"
      ? team?.tasks ?? []
      : (team?.tasks ?? []).filter(item => item.agent_identity_ref.split("@")[0] === channel);
    const pending = channel === "supervisor"
      ? questions
      : questions.filter(item => item.agent_code === channel);
    return {
      channel,
      status: pending.length ? "NEEDS_INPUT" : tasks.find(item => item.status === "RUNNING")?.status ?? tasks[0]?.status ?? "PENDING",
      evidence_count: tasks.reduce((total, item) => total + (item.evidence_count ?? 0), 0),
      pending_count: pending.length,
      summary: tasks.find(item => item.summary)?.summary ?? "",
    };
  });
}

export function AgentConversationDock({
  runId,
  team,
  questions,
  onConversationUpdate,
}: {
  runId: string;
  team?: AgentTeamsRun;
  questions: Clarification[];
  onConversationUpdate(): void;
}) {
  const { locale, t } = useI18n();
  const [active, setActive] = useState<ConversationChannel>("supervisor");
  const [open, setOpen] = useState(false);
  const [channels, setChannels] = useState<ConversationChannelState[]>(() => fallbackStates(team, questions));
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [allowExternal, setAllowExternal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const messageKey = useRef(crypto.randomUUID());
  const answerKey = useRef(crypto.randomUUID());
  const lastTrigger = useRef<HTMLButtonElement | null>(null);
  const closeButton = useRef<HTMLButtonElement | null>(null);

  const load = useCallback(async () => {
    try {
      const projection = await browserApi().listRunConversations(runId);
      setChannels(projection.channels);
      setMessages(projection.messages);
      setError(undefined);
    } catch (cause) {
      setChannels(fallbackStates(team, questions));
      setError(cause instanceof Error ? cause.message : t("Conversation history is temporarily unavailable."));
    }
  }, [questions, runId, t, team]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    messageKey.current = crypto.randomUUID();
    answerKey.current = crypto.randomUUID();
    setDraft("");
    setAnswers({});
    setError(undefined);
  }, [runId]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      window.setTimeout(() => lastTrigger.current?.focus(), 0);
    };
    window.addEventListener("keydown", onKeyDown);
    window.setTimeout(() => closeButton.current?.focus(), 0);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  const state = channels.find(item => item.channel === active) ?? fallbackStates(team, questions)[0];
  const activeMessages = messages.filter(item => item.channel === active);
  const pending = active === "supervisor" ? questions : questions.filter(item => item.agent_code === active);
  const definition = RUN_CONVERSATION_CHANNELS.find(item => item.channel === active)!;

  function toggle(channel: ConversationChannel, trigger: HTMLButtonElement) {
    lastTrigger.current = trigger;
    if (open && channel === active) {
      setOpen(false);
      window.setTimeout(() => trigger.focus(), 0);
      return;
    }
    setActive(channel);
    setOpen(true);
  }

  async function submitMessage() {
    const message = draft.trim();
    if (!message) return;
    if (active === "supervisor" && !allowExternal) {
      setError(t("Confirm that the configured Intake Model may process this request. M7-A Recorded acceptance does not call a live model."));
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      await browserApi().submitRunConversationMessage(
        runId,
        active,
        message,
        active === "supervisor" && allowExternal,
        messageKey.current,
      );
      messageKey.current = crypto.randomUUID();
      setDraft("");
      await load();
      onConversationUpdate();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Conversation message submission failed."));
    } finally {
      setBusy(false);
    }
  }

  async function submitAnswers() {
    const payload = pending.map(item => ({
      request_id: item.request_id,
      answer: (answers[item.request_id] ?? "").trim(),
    }));
    if (!payload.length || payload.some(item => !item.answer)) {
      setError(t("Answer every question blocking the current evaluation."));
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      await browserApi().answerClarifications(runId, payload, answerKey.current);
      answerKey.current = crypto.randomUUID();
      setAnswers({});
      await load();
      onConversationUpdate();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Additional information submission failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="conversation-dock" data-open={open || undefined} aria-label={t("Run conversations")}>
      <div className="conversation-tabs" role="tablist" aria-label={t("Run conversations")} aria-orientation="vertical">
        {RUN_CONVERSATION_CHANNELS.map((item, index) => {
          const itemState = channels.find(value => value.channel === item.channel);
          const selected = item.channel === active;
          return (
            <button
              key={item.channel}
              ref={selected ? lastTrigger : undefined}
              id={`conversation-tab-${item.channel}`}
              className="conversation-tab"
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`conversation-panel-${item.channel}`}
              tabIndex={selected ? 0 : -1}
              data-state={(itemState?.status ?? "PENDING").toLowerCase()}
              onClick={event => toggle(item.channel, event.currentTarget)}
              onKeyDown={event => {
                const target = event.key === "ArrowDown" || event.key === "ArrowRight"
                  ? (index + 1) % RUN_CONVERSATION_CHANNELS.length
                  : event.key === "ArrowUp" || event.key === "ArrowLeft"
                    ? (index + RUN_CONVERSATION_CHANNELS.length - 1) % RUN_CONVERSATION_CHANNELS.length
                    : event.key === "Home" ? 0 : event.key === "End" ? RUN_CONVERSATION_CHANNELS.length - 1 : undefined;
                if (target === undefined) return;
                event.preventDefault();
                const next = RUN_CONVERSATION_CHANNELS[target].channel;
                setActive(next);
                document.getElementById(`conversation-tab-${next}`)?.focus();
              }}
            >
              <ChannelGlyph channel={item.channel} />
              <span>{t(item.shortLabel)}</span>
              {(itemState?.pending_count ?? 0) > 0 && (
                <span className="conversation-badge" aria-label={t("{count} open question(s)", { count: itemState!.pending_count })}>
                  {itemState!.pending_count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {open && (
        <section
          id={`conversation-panel-${active}`}
          className="conversation-drawer"
          role="tabpanel"
          aria-labelledby={`conversation-tab-${active}`}
        >
          <header className="conversation-drawer-head">
            <div className="conversation-identity">
              <span className="conversation-avatar"><ChannelGlyph channel={active} /></span>
              <span>
                <span className="bearing">{t("Controlled conversation")}</span>
                <h2>{t(definition.label)}</h2>
                <small>{t(state.evidence_count === 1 ? "1 evidence item" : "{count} evidence items", { count: state.evidence_count })}</small>
              </span>
            </div>
            <div className="conversation-head-actions">
              <StatusPill value={state.status} />
              <button
                ref={closeButton}
                type="button"
                className="quiet conversation-close"
                onClick={() => {
                  setOpen(false);
                  window.setTimeout(() => lastTrigger.current?.focus(), 0);
                }}
                aria-label={t("Close conversation")}
              >
                ×
              </button>
            </div>
          </header>

          {state.summary && <p className="conversation-summary">{formatUserVisibleAgentText(state.summary, locale, active)}</p>}

          <div className="conversation-log" aria-live="polite">
            {activeMessages.length === 0 && pending.length === 0 && (
              <div className="conversation-empty">
                <strong>{t("No messages in this conversation yet.")}</strong>
                <p>{t("Add facts here. The control plane records and scopes them before any Agent work changes.")}</p>
              </div>
            )}
            {activeMessages.map(message => (
              <div className="conversation-message" data-role={message.role.toLowerCase()} key={message.message_id}>
                <span>{message.role === "USER" ? t("You") : t(definition.label)}</span>
                <p>{message.role === "USER" ? message.text : formatUserVisibleAgentText(message.text, locale, active)}</p>
                {message.role === "USER" && (
                  <small>{message.route_state === "ROUTED"
                    ? t("Recorded and routed to relevant pending work")
                    : message.route_state === "WAITING_FOR_USER"
                      ? t("Recorded · waiting for your confirmation")
                      : message.route_state === "NEEDS_ATTENTION"
                        ? t("Recorded · execution remains safely paused")
                        : t("Recorded · pending controlled routing")}</small>
                )}
              </div>
            ))}
            {pending.map(question => (
              <label className="conversation-question" key={question.request_id}>
                <span className="field-name">{translateGapQuestion(locale, question.field, question.question)}</span>
                <span className="field-hint">{locale === "zh-CN" ? t("This information affects the prediction scope and evidence strength.") : question.why_blocking}</span>
                <textarea
                  maxLength={4000}
                  value={answers[question.request_id] ?? ""}
                  onChange={event => setAnswers(current => ({ ...current, [question.request_id]: event.target.value }))}
                />
              </label>
            ))}
          </div>

          <footer className="conversation-composer">
            {pending.length > 0 && (
              <button type="button" onClick={() => void submitAnswers()} disabled={busy}>
                {busy ? t("Saving…") : t("Answer and continue")}
              </button>
            )}
            <form
              onSubmit={event => {
                event.preventDefault();
                void submitMessage();
              }}
            >
              <textarea
                aria-label={t("Message to {name}", { name: t(definition.label) })}
                placeholder={t("Add facts or request a change…")}
                maxLength={30000}
                value={draft}
                onChange={event => setDraft(event.target.value)}
              />
              {active === "supervisor" && (
                <label className="supervisor-consent">
                  <input type="checkbox" checked={allowExternal} onChange={event => setAllowExternal(event.target.checked)} />
                  {t("Allow the configured Intake Model to process this message")}
                </label>
              )}
              <div className="conversation-send-row">
                <small>{t("Messages are recorded first; an Agent response appears only after durable work is committed.")}</small>
                <button disabled={busy || !draft.trim()}>{busy ? t("Submitting…") : t("Send")}</button>
              </div>
            </form>
            {error && <LocalizedErrorMessage value={error} className="error-banner" />}
          </footer>
        </section>
      )}
    </aside>
  );
}
