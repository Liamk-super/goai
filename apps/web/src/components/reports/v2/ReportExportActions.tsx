"use client";

import { useState } from "react";

import {
  boundedIdempotencyKey,
  browserApi,
  createPublicDemoReportExport,
  getPublicDemoReportExport,
  getPublicDemoReportExportReadUrl,
  type AgentReportSummary,
  type ReportExportArtifact,
  type ReportExportRequest,
} from "../../../lib/api-client";
import { useI18n } from "../../i18n/LocaleProvider";

const delay = (milliseconds: number) => new Promise(resolve => window.setTimeout(resolve, milliseconds));
const EXPORT_POLL_ATTEMPTS = 240;
const EXPORT_POLL_INTERVAL_MS = 1_000;

export function ReportExportActions({
  reportId,
  agentCode,
  view = "FULL",
  allowPackage = false,
  publicToken,
}: {
  reportId: string;
  agentCode?: AgentReportSummary["agent_code"];
  view?: ReportExportRequest["view"];
  allowPackage?: boolean;
  publicToken?: string;
}) {
  const { locale, t } = useI18n();
  const [includeEvidence, setIncludeEvidence] = useState(false);
  const [busyKind, setBusyKind] = useState<"PDF" | "PACKAGE">();
  const [error, setError] = useState<string>();

  const waitForExport = async (artifact: ReportExportArtifact) => {
    let current = artifact;
    for (let attempt = 0; attempt < EXPORT_POLL_ATTEMPTS && current.status !== "COMPLETED"; attempt += 1) {
      if (current.status === "FAILED") throw new Error(current.error_code ?? t("Export failed"));
      await delay(EXPORT_POLL_INTERVAL_MS);
      current = publicToken
        ? await getPublicDemoReportExport(publicToken, current.export_id)
        : await browserApi().getReportExport(current.export_id);
    }
    if (current.status !== "COMPLETED") throw new Error(t("Export is taking longer than expected"));
    return current;
  };

  const download = async (kind: "PDF" | "PACKAGE") => {
    setBusyKind(kind);
    setError(undefined);
    const request: ReportExportRequest = kind === "PACKAGE"
      ? { kind: "PACKAGE", agent_code: null, view: "FULL", locale, include_evidence: includeEvidence }
      : {
          kind: agentCode ? "SPECIALIST" : "SUPERVISOR",
          agent_code: agentCode ?? null,
          view,
          locale,
          include_evidence: false,
        };
    const key = boundedIdempotencyKey(
      "report-export-v3",
      reportId,
      JSON.stringify(request),
    );
    try {
      const created = publicToken
        ? await createPublicDemoReportExport(publicToken, reportId, request, key)
        : await browserApi().createReportExport(reportId, request, key);
      const completed = await waitForExport(created);
      const read = publicToken
        ? await getPublicDemoReportExportReadUrl(publicToken, completed.export_id)
        : await browserApi().getReportExportReadUrl(completed.export_id);
      const anchor = document.createElement("a");
      anchor.href = read.read_url;
      anchor.download = kind === "PACKAGE" ? "LaunchScope-report-package.zip" : "LaunchScope-report.pdf";
      anchor.rel = "noopener";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Export failed"));
    } finally {
      setBusyKind(undefined);
    }
  };

  return (
    <section className="report-export-actions" aria-label={t("Report export")}>
      <button type="button" className="button secondary" disabled={Boolean(busyKind)} onClick={() => void download("PDF")}>
        {busyKind === "PDF" ? t("Preparing export…") : t("Export PDF")}
      </button>
      {allowPackage && (
        <>
          <label className="report-export-evidence-option">
            <input
              type="checkbox"
              checked={includeEvidence}
              disabled={Boolean(busyKind)}
              onChange={event => setIncludeEvidence(event.target.checked)}
            />
            <span>{t("Include Evidence originals")}</span>
          </label>
          <button type="button" className="button" disabled={Boolean(busyKind)} onClick={() => void download("PACKAGE")}>
            {busyKind === "PACKAGE" ? t("Preparing export…") : t("Download complete report package")}
          </button>
        </>
      )}
      {error && <p className="error-banner" role="alert">{error}</p>}
    </section>
  );
}
