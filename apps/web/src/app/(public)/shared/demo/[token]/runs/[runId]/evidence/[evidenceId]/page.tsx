"use client";

import { use, useEffect, useState } from "react";

import { useI18n } from "../../../../../../../../../components/i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../../../../../../../../../components/i18n/LocalizedErrorMessage";
import { PublicDemoShell } from "../../../../../../../../../components/reports/PublicDemoShell";
import { apiBase } from "../../../../../../../../../lib/api-client";

type PublicEvidence = {
  evidence_id: string;
  run_id: string;
  sha256: string;
  mime_type: string;
  read_url: string;
  expires_in_seconds: number;
};

export default function PublicEvidencePage({
  params,
}: {
  params: Promise<{ token: string; runId: string; evidenceId: string }>;
}) {
  const { token, runId, evidenceId } = use(params);
  const { t } = useI18n();
  const [evidence, setEvidence] = useState<PublicEvidence>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    void fetch(`${apiBase()}/api/v1/public/demo/v2/evidence/${encodeURIComponent(evidenceId)}/read-url?token=${encodeURIComponent(token)}`)
      .then(async response => {
        if (!response.ok) throw new Error(t("This evidence link is invalid or revoked."));
        const value = await response.json() as PublicEvidence;
        if (value.run_id !== runId) throw new Error(t("This evidence does not belong to the shared Run."));
        return value;
      })
      .then(setEvidence)
      .catch(cause => setError(cause instanceof Error ? cause.message : t("Evidence loading failed")));
  }, [evidenceId, runId, token, t]);

  return (
    <PublicDemoShell>
      <main className="workspace-main public-evidence-page">
        <header className="student-report-heading"><div><span className="bearing">{t("Public evidence")}</span><h1>{t("Evidence details")}</h1></div></header>
        {error && <LocalizedErrorMessage value={error} className="error-banner" />}
        {!error && !evidence && <div className="empty-state"><strong>{t("Requesting a short-lived evidence link…")}</strong></div>}
        {evidence && (
          <section className="plate">
            <dl className="agent-report-metrics">
              <div><dt>{t("Evidence ID")}</dt><dd>{evidence.evidence_id}</dd></div>
              <div><dt>{t("File type")}</dt><dd>{evidence.mime_type}</dd></div>
              <div><dt>{t("Link lifetime")}</dt><dd>{evidence.expires_in_seconds}s</dd></div>
            </dl>
            <p>{t("Raw evidence opens as a download and is never executed inside LaunchScope.")}</p>
            <a className="button" href={evidence.read_url} download rel="noopener noreferrer">{t("Download evidence original")}</a>
          </section>
        )}
      </main>
    </PublicDemoShell>
  );
}
