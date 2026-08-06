"use client";

import { use, useState } from "react";
import { browserApi } from "../../../../../lib/api-client";
import { ProfileConfirmation, type GapQuestion } from "../../../../../components/profile/ProfileConfirmation";
import { PageHeader, StatusPill } from "../../../../../components/shell/AppShell";
import { useI18n } from "../../../../../components/i18n/LocaleProvider";

export default function NewEvaluationPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { t } = useI18n();
  const { projectId } = use(params); const [versionId, setVersionId] = useState(""); const [questions, setQuestions] = useState<GapQuestion[]>([]); const [correlationId, setCorrelationId] = useState(""); const [error, setError] = useState<string>(); const [runId, setRunId] = useState(""); const [busy, setBusy] = useState(false);
  const step = runId ? 4 : questions.length ? 3 : versionId ? 2 : 1;
  async function submitMaterial(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(undefined); setBusy(true); const form = new FormData(event.currentTarget);
    try { let activeVersion = versionId; if (!activeVersion) { activeVersion = (await browserApi().createVersion(projectId, String(form.get("label") ?? ""))).product_version_id; setVersionId(activeVersion); }
      const file = form.get("material"); if (!(file instanceof File) || !file.size) throw new Error(t("Select a material before submission.")); const result = await browserApi().uploadMaterial(activeVersion, file); if (result.status !== "VALIDATED") throw new Error(t("Material is {status}; gap questions remain locked.", { status: result.status })); const gaps = await browserApi().gapQuestions(activeVersion); setCorrelationId(gaps.correlation_id); setQuestions(gaps.questions);
    } catch (cause) { setError(cause instanceof Error ? cause.message : t("Material submission failed")); } finally { setBusy(false); }
  }
  return <main><PageHeader eyebrow={t("Evaluation intake")} title={t("Turn material into a map.")} description={t("Bytes move directly to a short-lived private URL. The control plane accepts only the verified hash and metadata before gap analysis begins.")} />
    <div className="step-rail" aria-label={t("Step {step} of 4", { step })}>{["Version", "Upload", "Confirm", "Plan"].map((label,index)=><div className={`step ${index < step ? "active":""}`} key={label}>0{index+1} / {t(label)}</div>)}</div>
    {!questions.length && !runId && <section className="panel reveal"><form onSubmit={submitMaterial}><label>{t("Version label")}<input name="label" required={!versionId} disabled={Boolean(versionId)} placeholder="V1" /></label><label>{t("Primary material")}<input name="material" type="file" required accept="application/json,application/pdf,image/jpeg,image/png,image/webp,text/plain" /></label>{error && <p role="alert">{error}</p>}<button disabled={busy}>{busy ? t("Verifying object…") : t("Upload + diagnose gaps")}</button></form></section>}
    {questions.length > 0 && !runId && <section className="panel reveal"><ProfileConfirmation questions={questions} onConfirm={async answers => { await browserApi().answerGaps(versionId, correlationId, answers); await browserApi().confirmProfile(versionId); const run = await browserApi().plan(versionId); setRunId(run.run_id); }} /></section>}
    {runId && <section className="panel reveal"><p className="panel-kicker">{t("Control plane accepted")}</p><h2>{t("The Run is planned.")}</h2><StatusPill value="PLANNED" /><p className="lede">{t("PostgreSQL has committed the profile-confirmed Run. Execution still requires a frozen Manifest and budget.")}</p><a className="button" href={`/runs/${runId}`}>{t("Open command timeline")}</a></section>}
  </main>;
}
