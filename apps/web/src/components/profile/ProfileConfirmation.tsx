"use client";

import { useState } from "react";
import { useI18n } from "../i18n/LocaleProvider";

export type GapQuestion = { field: string; question: string; priority: number };

export function ProfileConfirmation({ questions, onConfirm }: { questions: GapQuestion[]; onConfirm(answers: Record<string, string>): Promise<void> }) {
  const { t } = useI18n();
  const [answers, setAnswers] = useState<Record<string, string>>({}); const [error, setError] = useState<string>(); const [busy, setBusy] = useState(false);
  return <form onSubmit={async event => { event.preventDefault(); setBusy(true); setError(undefined); try { await onConfirm(answers); } catch (cause) { setError(cause instanceof Error ? cause.message : t("Profile confirmation failed")); setBusy(false); } }}>
    <div><p className="panel-kicker">{t("Human checkpoint")}</p><h2>{t("Close the critical gaps.")}</h2><p className="lede">{t("Answers become durable facts only after this explicit confirmation. Model inference alone cannot advance the Run.")}</p></div>
    {questions.map(question => <label key={question.field}><span>{String(question.priority).padStart(2,"0")} · {question.question}</span><textarea required value={answers[question.field] ?? ""} onChange={event => setAnswers({ ...answers, [question.field]: event.target.value })} /></label>)}
    {error && <p role="alert">{error}</p>}<button disabled={busy}>{busy ? t("Freezing profile…") : t("Confirm profile + plan run")}</button>
  </form>;
}
