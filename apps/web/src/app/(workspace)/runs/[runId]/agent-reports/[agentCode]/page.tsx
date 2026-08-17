"use client";

import { use, useEffect, useState } from "react";
import { AgentReportDocument } from "../../../../../../components/reports/AgentReportsPanel";
import { useI18n } from "../../../../../../components/i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../../../../../../components/i18n/LocalizedErrorMessage";
import { SpecialistReportV2 } from "../../../../../../components/reports/v2/SpecialistReportV2";
import { SpecialistReportV3 } from "../../../../../../components/reports/v3/SpecialistReportV3";
import {
  browserApi,
  type AgentReportDisplay,
  type AgentReportSummary,
  type SpecialistReportV2Projection,
  type SpecialistReportV3Projection,
} from "../../../../../../lib/api-client";

const AGENT_CODES: AgentReportSummary["agent_code"][] = [
  "user-evidence",
  "product-engineering",
  "business-investment",
  "evidence-auditor",
];

function isAgentCode(value: string): value is AgentReportSummary["agent_code"] {
  return AGENT_CODES.some(code => code === value);
}

function isSpecialistV2(report: AgentReportDisplay): report is SpecialistReportV2Projection {
  return "report_schema_version" in report && report.report_schema_version === "2.0";
}

function isSpecialistV3(report: AgentReportDisplay): report is SpecialistReportV3Projection {
  return "report_schema_version" in report && report.report_schema_version === "3.0";
}

export default function AgentReportPage({
  params,
}: {
  params: Promise<{ runId: string; agentCode: string }>;
}) {
  const { runId, agentCode } = use(params);
  const { t } = useI18n();
  const [report, setReport] = useState<AgentReportDisplay>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    let cancelled = false;
    setReport(undefined);
    setError(undefined);
    if (!isAgentCode(agentCode)) {
      setError(t("The specialist report is temporarily unavailable"));
      return () => { cancelled = true; };
    }
    void browserApi().getAgentReportForDisplay(runId, agentCode).then(result => {
      if (!cancelled) setReport(result);
    }).catch(cause => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : t("Agent report loading failed"));
    });
    return () => { cancelled = true; };
  }, [agentCode, runId, t]);

  return (
    <main className="workspace-main agent-report-page">
      <header className="student-report-heading agent-report-page-heading">
        <div>
          <span className="bearing">{t("1+4 Agent specialist report")}</span>
          <h1>{report ? (isSpecialistV2(report) || isSpecialistV3(report) ? report.document.product_title : report.title) : t("Specialist report")}</h1>
        </div>
        <a href={report && (isSpecialistV2(report) || isSpecialistV3(report)) ? `/reports/${report.projection.supervisor_report_id}#agent-reports` : `/runs/${encodeURIComponent(runId)}`}>
          ← {report && (isSpecialistV2(report) || isSpecialistV3(report)) ? t("Return to project lead report") : t("Return to prediction")}
        </a>
      </header>

      {report ? (
        isSpecialistV3(report)
          ? <SpecialistReportV3 report={report} />
          : isSpecialistV2(report)
          ? <SpecialistReportV2 report={report} />
          : <article className="plate agent-report-document"><AgentReportDocument report={report} /></article>
      ) : (
        <section className="plate agent-report-loading" aria-live="polite">
          <p className="plate-kicker">{t("Verified report artifact")}</p>
          <h2>{error ? t("The specialist report is temporarily unavailable") : t("Loading specialist report…")}</h2>
          {error && <LocalizedErrorMessage value={error} className="error-banner" />}
        </section>
      )}
    </main>
  );
}
