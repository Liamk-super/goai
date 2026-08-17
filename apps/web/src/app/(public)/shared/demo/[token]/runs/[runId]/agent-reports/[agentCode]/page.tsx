"use client";

import { use, useEffect, useState } from "react";

import { useI18n } from "../../../../../../../../../components/i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../../../../../../../../../components/i18n/LocalizedErrorMessage";
import { PublicDemoShell } from "../../../../../../../../../components/reports/PublicDemoShell";
import { SpecialistReportV2 } from "../../../../../../../../../components/reports/v2/SpecialistReportV2";
import { SpecialistReportV3 } from "../../../../../../../../../components/reports/v3/SpecialistReportV3";
import { apiBase, type SpecialistReportV2Projection, type SpecialistReportV3Projection } from "../../../../../../../../../lib/api-client";

export default function PublicSpecialistReportPage({
  params,
}: {
  params: Promise<{ token: string; runId: string; agentCode: string }>;
}) {
  const { token, runId, agentCode } = use(params);
  const { t } = useI18n();
  const [report, setReport] = useState<SpecialistReportV2Projection | SpecialistReportV3Projection>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    const suffix = `${encodeURIComponent(agentCode)}?token=${encodeURIComponent(token)}`;
    void fetch(`${apiBase()}/api/v1/public/demo/v3/agent-reports/${suffix}`)
      .then(async response => {
        if (response.ok) return response.json() as Promise<SpecialistReportV3Projection>;
        if (response.status !== 404) throw new Error(t("This specialist report link is invalid or revoked."));
        const v2 = await fetch(`${apiBase()}/api/v1/public/demo/v2/agent-reports/${suffix}`);
        if (!v2.ok) throw new Error(t("This specialist report link is invalid or revoked."));
        return v2.json() as Promise<SpecialistReportV2Projection>;
      })
      .then(value => {
        if (value.document.run_id !== runId || value.document.agent_code !== agentCode) {
          throw new Error(t("This specialist report does not belong to the shared Run."));
        }
        return value;
      })
      .then(setReport)
      .catch(cause => setError(cause instanceof Error ? cause.message : t("Agent report loading failed")));
  }, [agentCode, runId, token, t]);

  return (
    <PublicDemoShell>
      <main className="workspace-main agent-report-page">
        <header className="student-report-heading agent-report-page-heading">
          <div><span className="bearing">{t("Public specialist report")}</span><h1>{report?.document.product_title ?? t("Specialist report")}</h1></div>
          {report && <a href={`/shared/demo/${encodeURIComponent(token)}/reports/${report.projection.supervisor_report_id}#agent-reports`}>← {t("Return to project lead report")}</a>}
        </header>
        {error && <LocalizedErrorMessage value={error} className="error-banner" />}
        {!error && !report && <div className="empty-state"><strong>{t("Loading specialist report…")}</strong></div>}
        {report?.report_schema_version === "3.0" ? (
          <SpecialistReportV3
            report={report}
            evidenceHrefFor={evidenceId => `/shared/demo/${encodeURIComponent(token)}/runs/${encodeURIComponent(runId)}/evidence/${evidenceId}`}
            publicToken={token}
          />
        ) : report && (
          <SpecialistReportV2
            report={report}
            evidenceHrefFor={evidenceId => `/shared/demo/${encodeURIComponent(token)}/runs/${encodeURIComponent(runId)}/evidence/${evidenceId}`}
            publicToken={token}
          />
        )}
      </main>
    </PublicDemoShell>
  );
}
