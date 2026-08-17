"use client";

import { useRef, useState } from "react";

import {
  browserApi,
  type Clarification,
  type ClarificationAnswerResult,
  type Run,
  type SupervisorMessageResult,
} from "../../lib/api-client";
import { useI18n } from "../i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../i18n/LocalizedErrorMessage";

type ChatEntry = {
  id: string;
  role: "supervisor" | "user";
  text: string;
};

export function SupervisorChatPanel({
  run,
  questions,
  onRequirementResult,
  onClarificationAnswer,
}: {
  run: Run;
  questions: Clarification[];
  onRequirementResult(result: SupervisorMessageResult): void;
  onClarificationAnswer(result: ClarificationAnswerResult): void;
}) {
  const { t } = useI18n();
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [allowExternal, setAllowExternal] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const messageKey = useRef(crypto.randomUUID());
  const clarificationKey = useRef(crypto.randomUUID());

  async function submitMessage() {
    const message = draft.trim();
    if (!message) return;
    if (!allowExternal) {
      setError(t("Confirm that the configured Intake Model may process this request. M7-A Recorded acceptance does not call a live model."));
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const result = await browserApi().submitSupervisorMessage(
        run.project_id,
        run.product_version_id,
        message,
        allowExternal,
        messageKey.current,
      );
      messageKey.current = crypto.randomUUID();
      setEntries(current => [
        ...current,
        { id: result.message_id, role: "user", text: message },
        {
          id: `${result.message_id}:reply`,
          role: "supervisor",
          text: result.questions.length
            ? result.questions.join("\n")
            : run.status === "PLANNED"
              ? t("The request is now an authoritative brief and is entering controlled planning.")
              : t("The control plane recorded the update for evaluation; it will not directly rewrite tasks already started or completed."),
        },
      ]);
      setDraft("");
      onRequirementResult(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Project lead message submission failed"));
    } finally {
      setBusy(false);
    }
  }

  async function submitClarifications() {
    const payload = questions.map(item => ({
      request_id: item.request_id,
      answer: (answers[item.request_id] ?? "").trim(),
    }));
    if (payload.some(item => !item.answer)) {
      setError(t("Answer every question blocking the current evaluation."));
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const result = await browserApi().answerClarifications(run.run_id, payload, clarificationKey.current);
      clarificationKey.current = crypto.randomUUID();
      setEntries(current => [
        ...current,
        ...payload.map(item => ({ id: item.request_id, role: "user" as const, text: item.answer })),
        {
          id: `${result.run_id}:resumed:${Date.now()}`,
          role: "supervisor",
          text: t("Saved to the durable profile; only {count} affected tasks were re-dispatched.", { count: result.affected_task_ids.length }),
        },
      ]);
      setAnswers({});
      onClarificationAnswer(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Additional information submission failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="supervisor-chat plate" aria-label={t("Project lead conversation")}>
      <header className="supervisor-chat-head">
        <span className="supervisor-seal" aria-hidden="true">{t("Project lead")}</span>
        <span>
          <span className="bearing">{t("Project lead")}</span>
          <strong>{t("Project lead")}</strong>
          <small>{t("Helps clarify the project and explain the result")}</small>
        </span>
      </header>

      <div className="supervisor-chat-log" aria-live="polite">
        <div className="supervisor-message" data-role="supervisor">
          <span>{t("Project lead")}</span>
          <p>{run.status === "PLANNED"
            ? t("Building the supervisor plan from the confirmed profile and validation tasks. No repeated intake is required.")
            : questions.length > 0
              ? t("A specialist found a genuinely blocking gap. Answering it resumes only the affected work.")
              : t("The confirmed brief is active. The project lead is planning or coordinating the evaluation team.")}</p>
        </div>
        {entries.map(entry => (
          <div className="supervisor-message" data-role={entry.role} key={entry.id}>
            <span>{entry.role === "supervisor" ? t("Project lead") : t("You")}</span>
            <p>{entry.text}</p>
          </div>
        ))}
        {questions.map(question => (
          <label className="supervisor-question" key={question.request_id}>
            <span className="field-name">{question.question}</span>
            <span className="field-hint">{question.why_blocking}</span>
            <textarea
              value={answers[question.request_id] ?? ""}
              maxLength={4000}
              onChange={event => setAnswers(current => ({ ...current, [question.request_id]: event.target.value }))}
            />
          </label>
        ))}
      </div>

      {questions.length > 0 ? (
        <button type="button" onClick={() => void submitClarifications()} disabled={busy}>
          {busy ? t("Saving…") : t("Answer and continue")}
        </button>
      ) : run.status !== "PLANNED" ? (
        <details className="supervisor-supplement">
          <summary>{t("Supplement or change requirements")}</summary>
          <form
            className="supervisor-composer"
            onSubmit={event => {
              event.preventDefault();
              void submitMessage();
            }}
          >
            <textarea
              aria-label={t("Message to the project lead")}
              placeholder={t("Add facts or request a change…")}
              value={draft}
              maxLength={30000}
              onChange={event => setDraft(event.target.value)}
            />
            <label className="supervisor-consent">
              <input
                type="checkbox"
                checked={allowExternal}
                onChange={event => setAllowExternal(event.target.checked)}
              />
              {t("Allow the configured Intake Model to process this message")}
            </label>
            <button disabled={busy || !draft.trim()}>{busy ? t("Submitting…") : t("Send to project lead")}</button>
          </form>
        </details>
      ) : null}
      {error && <LocalizedErrorMessage value={error} className="error-banner" />}
    </aside>
  );
}
