"use client";

import { useState } from "react";
import type { ReportClaimV2, SpecialistReportV2Projection } from "../../../lib/api-client";
import { presentReportText } from "../../../lib/report-copy";
import { useI18n } from "../../i18n/LocaleProvider";
import { InlineCitation } from "./InlineCitation";
import { ReportActions } from "./ReportActions";
import { ReportExportActions } from "./ReportExportActions";
import { SpecialistViewTabs, type SpecialistView } from "./SpecialistViewTabs";

const auditLabelKeys = {
  VERIFIED: "Evidence sufficient",
  DOWNGRADED: "Evidence limited; certainty reduced",
  PENDING_VALIDATION: "More evidence needed",
  CONFLICTED: "Evidence conflict",
} as const;

export function SpecialistReportV2({
  report,
  evidenceHrefFor,
  publicToken,
}: {
  report: SpecialistReportV2Projection;
  evidenceHrefFor?: (evidenceId: string) => string;
  publicToken?: string;
}) {
  const { locale, t, status } = useI18n();
  const [view, setView] = useState<SpecialistView>("summary");
  const document = report.document;
  const citations = new Map(document.citations.map(citation => [citation.citation_id, citation]));
  const sources = new Map(document.source_directory.map(source => [source.source_locator_id, source]));
  const summaryIds = new Set(document.executive_summary);
  const visibleClaims = view === "summary"
    ? document.claims.filter(claim => summaryIds.has(claim.claim_id))
    : document.claims;
  const renderClaim = (claim: ReportClaimV2) => (
    <article className="agent-report-finding specialist-v22-claim" key={claim.claim_id} data-claim-id={claim.claim_id}>
      <header className="agent-report-finding-head">
        <span className="status-pill">
          {document.agent_code === "evidence-auditor" ? t(auditLabelKeys[claim.status]) : status(claim.status)}
        </span>
        <span>{status(claim.decision_relevance)}</span>
      </header>
      <div className="agent-report-claim">
        {presentReportText(locale, claim.text)}{" "}
        {claim.citation_ids.map(citationId => {
          const citation = citations.get(citationId);
          if (!citation) return null;
          return (
            <InlineCitation
              key={citationId}
              citation={citation}
              source={citation.source_locator_id ? sources.get(citation.source_locator_id) : undefined}
              evidenceHref={(evidenceHrefFor ?? (id => `/evidence/${id}`))(citation.evidence_id)}
            />
          );
        })}
      </div>
      {claim.status === "PENDING_VALIDATION" && <small>{t("Pending validation · excluded from scoring and the main recommendation")}</small>}
    </article>
  );

  return (
    <article
      className="plate agent-report-document specialist-v22-document"
      data-content-sha256={report.integrity.canonical_sha256}
      data-report-ready="true"
    >
      <SpecialistViewTabs view={view} onChange={setView} />
      <ReportExportActions
        reportId={report.projection.supervisor_report_id}
        agentCode={document.agent_code}
        view={view === "summary" ? "SUMMARY" : "FULL"}
        publicToken={publicToken}
      />
      <header className="agent-report-section specialist-v22-heading">
        <span className="plate-kicker">{status(document.agent_code)}</span>
        <h2>{document.product_title}</h2>
      </header>
      <dl className="agent-report-metrics">
        {document.metrics.map(metric => <div key={metric.key}><dt>{presentReportText(locale, metric.label)}</dt><dd>{presentReportText(locale, String(metric.value))}</dd></div>)}
      </dl>
      <section className="agent-report-section">
        <div className="agent-report-findings">{visibleClaims.map(renderClaim)}</div>
      </section>
      {view === "full" && (
        <>
          <ReportActions actions={document.actions} />
          <section className="agent-report-section report-v22-sources">
            <span className="plate-kicker">{t("Source directory")}</span>
            <ol>{document.source_directory.map(source => (
              <li key={source.source_locator_id}><span>{status(source.source_kind)}</span><div><strong>{source.title}</strong><small>{source.publisher ?? t("Uploaded project material")}</small></div></li>
            ))}</ol>
          </section>
        </>
      )}
    </article>
  );
}
