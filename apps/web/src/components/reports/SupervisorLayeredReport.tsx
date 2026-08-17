"use client";

import { useEffect, useState } from "react";
import type { ReportDisplay, ReportV2Projection, ReportV3Projection } from "../../lib/api-client";
import { EvidenceChain } from "../evidence/EvidenceChain";
import { AgentReportsPanel } from "./AgentReportsPanel";
import { useI18n } from "../i18n/LocaleProvider";
import { formatStudentReport } from "../../lib/user-report-formatter";
import { SupervisorReportV2 } from "./v2/SupervisorReportV2";
import { SupervisorReportV3 } from "./v3/SupervisorReportV3";

function isReportV2(report: ReportDisplay): report is ReportV2Projection {
  return "report_schema_version" in report && report.report_schema_version === "2.0";
}

function isReportV3(report: ReportDisplay): report is ReportV3Projection {
  return "report_schema_version" in report && report.report_schema_version === "3.0";
}

export function SupervisorLayeredReport({
  report,
  readOnly = false,
  runHref,
  agentHrefFor,
  evidenceHrefFor,
  publicToken,
}: {
  report: ReportDisplay;
  readOnly?: boolean;
  runHref?: string;
  agentHrefFor?: (agentCode: "user-evidence" | "product-engineering" | "business-investment" | "evidence-auditor") => string;
  evidenceHrefFor?: (evidenceId: string) => string;
  publicToken?: string;
}) {
  const { locale, t, status } = useI18n();
  const [developerMode, setDeveloperMode] = useState(false);

  useEffect(() => {
    if (!readOnly) setDeveloperMode(new URLSearchParams(window.location.search).get("debug") === "1");
  }, [readOnly]);

  if (isReportV3(report)) {
    return (
      <SupervisorReportV3
        report={report}
        readOnly={readOnly}
        runHref={runHref}
        agentHrefFor={agentHrefFor}
        evidenceHrefFor={evidenceHrefFor}
        publicToken={publicToken}
      />
    );
  }

  if (isReportV2(report)) {
    return (
      <SupervisorReportV2
        report={report}
        readOnly={readOnly}
        runHref={runHref}
        agentHrefFor={agentHrefFor}
        evidenceHrefFor={evidenceHrefFor}
        publicToken={publicToken}
      />
    );
  }

  const student = formatStudentReport(report, locale);

  return (
    <main className="workspace-main supervisor-report-page student-report-page">
      <header className="student-report-heading">
        <div>
          <span className="bearing">{t("Prediction target")} {report.project_name ?? t("Current project")}</span>
          <h1>{t("Prediction report")}</h1>
        </div>
        <a href={runHref ?? `/runs/${report.run_id}`}>← {t("Return to prediction")}</a>
      </header>

      <section className="plate supervisor-report-hero student-report-hero">
        <p className="plate-kicker">{t("Final conclusion")}</p>
        <h2>{student.verdict}</h2>
        <p className="supervisor-report-summary">{student.summary}</p>
        {student.scoreLabel && <p className="student-score-label">{student.scoreLabel}</p>}
      </section>

      <section className="plate student-report-reasons">
        <p className="plate-kicker">{t("Why this conclusion")}</p>
        <ol>
          {student.reasons.map((reason, index) => <li key={reason}><span>0{index + 1}</span><p>{reason}</p></li>)}
        </ol>
      </section>

      <section className="student-opportunity-risk" aria-label={t("Largest opportunity and risk")}>
        <article className="plate plate-quiet">
          <p className="plate-kicker">{t("Largest opportunity")}</p>
          <p>{student.opportunity}</p>
        </article>
        <article className="plate plate-quiet" data-tone="attention">
          <p className="plate-kicker">{t("Largest risk")}</p>
          <p>{student.risk}</p>
        </article>
      </section>

      <section className="plate supervisor-action-plate">
        <p className="plate-kicker">{t("Action plan")}</p>
        <h2>{t("Prioritize these next")}</h2>
        <ol className="supervisor-action-list">
          {student.actions.map((action, index) => (
            <li key={action}><span>0{index + 1}</span><strong>{action}</strong></li>
          ))}
        </ol>
      </section>

      {student.gaps.length > 0 && (
        <section className="plate plate-quiet supervisor-gap-plate">
          <p className="plate-kicker">{t("Insufficient evidence")}</p>
          <h2>{t("Information still needed")}</h2>
          <ul>{student.gaps.map(item => <li key={item}>{item}</li>)}</ul>
        </section>
      )}

      {developerMode && (
        <details className="plate plate-quiet report-drawer supervisor-report-process" open>
          <summary>
            <span>{t("Developer details")}</span>
            <span className="g-meta">{status(report.recommendation)} · {report.report_id.slice(0, 8)}</span>
          </summary>
          <section>
            <h3>{t("Raw project lead synthesis")}</h3>
            <p>{report.layered_report?.summary}</p>
            <ul>{report.layered_report?.cross_domain_analysis.map(item => <li key={item}>{item}</li>)}</ul>
          </section>
          <section>
            <h3>{t("Audit record")}</h3>
            <ul className="record-list">
              {report.calibration_results?.map(item => (
                <li key={item.finding_id}>
                  <span><strong>{item.decision}</strong><span className="bearing">{item.reason}</span></span>
                  <span className="bearing">{item.finding_id.slice(0, 8)}</span>
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h3>{t("Finding → Evidence")}</h3>
            <EvidenceChain items={report.evidence_chain} readOnly={readOnly} />
          </section>
        </details>
      )}

      {!readOnly && <AgentReportsPanel runId={report.run_id} />}

      <p className="supervisor-report-footer-actions">
        {!readOnly && <a className="button" href={`/projects/${report.project_id}/new-evaluation`}>{t("Submit a new version and re-evaluate")}</a>}
        <a className={readOnly ? "button" : "button secondary"} href="/">{t("Return to wheel home")}</a>
      </p>
    </main>
  );
}
