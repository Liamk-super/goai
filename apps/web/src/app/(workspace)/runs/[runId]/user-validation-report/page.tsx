"use client";

import { use, useEffect, useState } from "react";

import {
  browserApi,
  type UserValidationResult,
} from "../../../../../lib/api-client";
import { useI18n } from "../../../../../components/i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../../../../../components/i18n/LocalizedErrorMessage";

export default function UserValidationFullReportPage({ params }: { params: Promise<{ runId: string }> }) {
  const { t } = useI18n();
  const { runId } = use(params);
  const [result, setResult] = useState<UserValidationResult>();
  const [fullReportHtml, setFullReportHtml] = useState<string>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void browserApi().getUserValidationResult(runId).then(async value => {
      if (cancelled) return;
      setResult(value);
      if (!value.presentation?.full.html.available) return;
      const report = await browserApi().getUserValidationReport(runId, "full", "html");
      if (!cancelled) setFullReportHtml(report.content);
    }).catch(cause => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : t("Complete report loading failed"));
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [runId]);

  async function download(variant: "summary" | "full", format: "html" | "markdown") {
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

  return (
    <main className="workspace-main">
      <div className="page-head">
        <span className="bearing">{t("User validation")} / {runId.slice(0, 8)}</span>
        <h1>{t("Complete user-validation report")}</h1>
        <p>{t("This is an independent verification artifact. The summary remains on the Run page, and raw machine JSON is the sole canonical source.")}</p>
      </div>

      {loading && <section className="plate"><p>{t("Validating and loading the complete report…")}</p></section>}
      {error && <LocalizedErrorMessage value={error} className="error-banner" />}

      {!loading && result && !result.presentation && (
        <section className="plate">
          <p className="plate-kicker">LEGACY RESULT · {result.schema_version}</p>
          <h2>{t("This result has no Presentation 0.4")}</h2>
          <p>{String(result.summary.result_summary ?? t("Legacy or blocked results provide only a summary and raw machine JSON."))}</p>
          <div className="form-actions" style={{ marginTop: 18 }}>
            <a className="button" href={result.report_url} target="_blank" rel="noreferrer">{t("Open raw machine JSON")}</a>
          </div>
        </section>
      )}

      {fullReportHtml && (
        <section className="plate">
          <div className="grid-auto">
            <dl className="readout"><dt>{t("Presentation")}</dt><dd>{result?.presentation?.version}</dd></dl>
            <dl className="readout"><dt>{t("Skill result hash")}</dt><dd>{result?.skill_result_sha256}</dd></dl>
          </div>
          <div className="form-actions" style={{ marginTop: 18, flexWrap: "wrap" }}>
            {(["summary", "full"] as const).flatMap(variant =>
              (["html", "markdown"] as const).map(format => (
                <button
                  key={`${variant}-${format}`}
                  type="button"
                  className="button secondary"
                  onClick={() => void download(variant, format)}
                >
                  {t("Download {variant}{format}", { variant: variant === "summary" ? t("summary") : t("complete"), format: format === "html" ? " HTML" : " Markdown" })}
                </button>
              )),
            )}
            {result && (
              <a className="button secondary" href={result.report_url} target="_blank" rel="noreferrer">
                {t("Open raw machine JSON")}
              </a>
            )}
          </div>
          <iframe
            title={t("Complete user-validation report")}
            sandbox=""
            referrerPolicy="no-referrer"
            srcDoc={fullReportHtml}
            style={{ width: "100%", minHeight: 1100, marginTop: 22, border: "1px solid var(--line)", borderRadius: 18, background: "white" }}
          />
        </section>
      )}

      {!loading && result?.presentation && !fullReportHtml && !error && (
        <section className="plate"><p>{t("The complete report is unavailable. The system will not synthesize a substitute from a legacy summary.")}</p></section>
      )}
    </main>
  );
}
