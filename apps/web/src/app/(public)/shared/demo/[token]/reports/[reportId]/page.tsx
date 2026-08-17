"use client";

import { use, useEffect, useState } from "react";

import { useI18n } from "../../../../../../../components/i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../../../../../../../components/i18n/LocalizedErrorMessage";
import { PublicDemoShell } from "../../../../../../../components/reports/PublicDemoShell";
import { SupervisorLayeredReport } from "../../../../../../../components/reports/SupervisorLayeredReport";
import { apiBase, type ReportDisplay } from "../../../../../../../lib/api-client";

export default function PublicDemoReportPage({
  params,
}: {
  params: Promise<{ token: string; reportId: string }>;
}) {
  const { token, reportId } = use(params);
  const { t } = useI18n();
  const [report, setReport] = useState<ReportDisplay>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    const query = `?token=${encodeURIComponent(token)}`;
    void fetch(`${apiBase()}/api/v1/public/demo/v3/reports/${reportId}${query}`)
      .then(async response => {
        if (response.ok) return response.json() as Promise<ReportDisplay>;
        if (response.status !== 404) throw new Error(t("This read-only report link is invalid or revoked."));
        const v2 = await fetch(`${apiBase()}/api/v1/public/demo/v2/reports/${reportId}${query}`);
        if (v2.ok) return v2.json() as Promise<ReportDisplay>;
        if (v2.status !== 404) throw new Error(t("This read-only report link is invalid or revoked."));
        const legacy = await fetch(`${apiBase()}/api/v1/public/demo/reports/${reportId}${query}`);
        if (!legacy.ok) throw new Error(t("This read-only report link is invalid or revoked."));
        return legacy.json() as Promise<ReportDisplay>;
      })
      .then(setReport)
      .catch(cause => setError(cause instanceof Error ? cause.message : t("Report loading failed")));
  }, [reportId, token]);

  const runId = report ? ("report_schema_version" in report ? report.document.run_id : report.run_id) : undefined;
  const runHref = runId ? `/shared/demo/${token}/runs/${runId}` : undefined;
  return (
      <PublicDemoShell>
        {error && <main className="workspace-main"><LocalizedErrorMessage value={error} className="error-banner" /></main>}
        {!error && !report && <main className="workspace-main"><div className="empty-state"><strong>{t("Reading sealed report…")}</strong></div></main>}
        {report && (
          <SupervisorLayeredReport
            report={report}
            readOnly
            runHref={runHref}
            agentHrefFor={agentCode => `/shared/demo/${encodeURIComponent(token)}/runs/${encodeURIComponent(runId ?? "")}/agent-reports/${agentCode}`}
            evidenceHrefFor={evidenceId => `/shared/demo/${encodeURIComponent(token)}/runs/${encodeURIComponent(runId ?? "")}/evidence/${evidenceId}`}
            publicToken={token}
          />
        )}
      </PublicDemoShell>
  );
}
