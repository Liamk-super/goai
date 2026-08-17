"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import type { ReportClaimV2, SpecialistReportV3Projection } from "../../../lib/api-client";
import { presentReportText } from "../../../lib/report-copy";
import { specialistPayloadSections, specialistViewFromQuery } from "../../../lib/report-v3-presentation";
import { useI18n } from "../../i18n/LocaleProvider";
import { InlineCitation } from "../v2/InlineCitation";
import { ReportExportActions } from "../v2/ReportExportActions";
import { SpecialistViewTabs, type SpecialistView } from "../v2/SpecialistViewTabs";
import {
  ActionCard,
  ConfidentialCover,
  DueDiligenceTable,
  EvidenceBadgeRow,
  InstitutionalReportShell,
  ReportFooter,
  RiskCallout,
  SourceDirectory,
} from "../institutional/InstitutionalReport";

export function SpecialistReportV3({
  report,
  evidenceHrefFor,
  publicToken,
}: {
  report: SpecialistReportV3Projection;
  evidenceHrefFor?: (evidenceId: string) => string;
  publicToken?: string;
}) {
  const { locale, t, status } = useI18n();
  const searchParams = useSearchParams();
  const [view, setView] = useState<SpecialistView>(() => specialistViewFromQuery(searchParams.get("view")));
  const document = report.document;
  const citations = new Map(document.citations.map(citation => [citation.citation_id, citation]));
  const sources = new Map(document.source_directory.map(source => [source.source_locator_id, source]));
  const claimById = new Map(document.claims.map(claim => [claim.claim_id, claim]));
  const summaryIds = new Set(document.executive_summary);
  const visibleClaims = view === "summary" ? document.claims.filter(claim => summaryIds.has(claim.claim_id)) : document.claims;
  const sections = specialistPayloadSections(document.domain_payload);
  const changeView = (nextView: SpecialistView) => {
    setView(nextView);
    const next = new URLSearchParams(searchParams.toString());
    next.set("view", nextView);
    window.history.replaceState(window.history.state, "", `${window.location.pathname}?${next}${window.location.hash}`);
  };
  const renderClaim = (claim: ReportClaimV2) => (
    <article className="agent-report-finding specialist-v22-claim" key={claim.claim_id} data-claim-id={claim.claim_id}>
      <header className="agent-report-finding-head"><span className="status-pill">{status(claim.status)}</span><span>{status(claim.decision_relevance)}</span></header>
      <div className="agent-report-claim">{presentReportText(locale, claim.text)} {claim.citation_ids.map(citationId => {
        const citation = citations.get(citationId);
        return citation && <InlineCitation key={citationId} citation={citation} source={citation.source_locator_id ? sources.get(citation.source_locator_id) : undefined} evidenceHref={(evidenceHrefFor ?? (id => `/evidence/${id}`))(citation.evidence_id)} />;
      })}</div>
      {claim.status === "PENDING_VALIDATION" && <small>{t("Pending validation · excluded from scoring and the main recommendation")}</small>}
    </article>
  );
  const citationLabelFor = (sourceId: string) => `[${document.citations.find(item => item.source_locator_id === sourceId)?.label ?? "—"}]`;
  const roleLabel = status(document.agent_code);

  return (
    <article className="plate agent-report-document specialist-v22-document specialist-v3-document institutional-specialist-document" data-content-sha256={report.integrity.canonical_sha256} data-report-ready="true">
      <InstitutionalReportShell>
        <ConfidentialCover
          locale={locale}
          title={document.product_title}
          reportId={document.report_id}
          runId={document.run_id}
          createdAt={report.projection.created_at}
          canonicalSha={report.integrity.canonical_sha256}
          kind="SPECIALIST"
          readOnly={Boolean(publicToken)}
        />
        <div className="institutional-specialist-toolbar">
          <SpecialistViewTabs view={view} onChange={changeView} />
          <ReportExportActions reportId={report.projection.supervisor_report_id} agentCode={document.agent_code} view={view === "summary" ? "SUMMARY" : "FULL"} publicToken={publicToken} />
        </div>
        <header className="agent-report-section specialist-v22-heading institutional-specialist-heading"><span className="plate-kicker">{roleLabel}</span><h2>{document.product_title}</h2><p>{t("This report can be read independently; every scored judgment remains bound to its citations and audit status.")}</p></header>
        <EvidenceBadgeRow locale={locale} covered={document.audit_summary.verified} required={document.audit_summary.verified + document.audit_summary.insufficient + document.audit_summary.needs_more + document.audit_summary.conflicted} confidence={document.audit_summary.verified + document.audit_summary.insufficient + document.audit_summary.needs_more + document.audit_summary.conflicted === 0 ? 0 : document.audit_summary.verified / (document.audit_summary.verified + document.audit_summary.insufficient + document.audit_summary.needs_more + document.audit_summary.conflicted)} profileRef={document.source_sha256.slice(0, 12)} />
        <DueDiligenceTable
          locale={locale}
          rows={document.metrics.map(metric => ({
            label: presentReportText(locale, metric.label),
            value: presentReportText(locale, String(metric.value)),
            detail: metric.claim_ids.length > 0 ? `${metric.claim_ids.length} ${t("Claim and citation index")}` : t("No evidence-backed findings are available in this section yet"),
          }))}
        />
        <section className="agent-report-section institutional-specialist-findings"><div className="institutional-section-heading"><span>01</span><div><p>{t("Final conclusion")}</p><h2>{t("Findings and evidence")}</h2></div></div><div className="agent-report-findings">{visibleClaims.map(renderClaim)}</div></section>

        <section className="agent-report-section specialist-v3-professional institutional-specialist-structure" aria-label={t("Professional analysis structure")}>
          {sections.map((section, index) => <article key={section.key}><span>{String(index + 2).padStart(2, "0")}</span><h3>{t(section.title)}</h3><ul>{section.items.map((item, itemIndex) => <li key={`${section.key}-${itemIndex}`}>{presentReportText(locale, item)}</li>)}</ul></article>)}
        </section>

        <section className="agent-report-section specialist-v3-risks">
          <RiskCallout locale={locale} title={t("Risks and validation gaps")}>
            <div className="agent-report-findings">{document.risks.map(claimId => claimById.get(claimId)).filter((claim): claim is ReportClaimV2 => Boolean(claim)).map(renderClaim)}</div>
            {document.risks.length === 0 && <p className="truthful-empty-state">{t("No evidence-backed findings are available in this section yet")}</p>}
          </RiskCallout>
        </section>

        {view === "full" && <>
          <section className="institutional-action-section"><div className="institutional-section-heading"><span>07</span><div><p>{t("Action plan")}</p><h2>{t("Prioritize these next")}</h2></div></div><div className="institutional-action-grid">{document.actions.map((action, index) => <ActionCard key={action.action_id} locale={locale} action={action} priority={index === 0 ? "P0" : index === 1 ? "P1" : "PX"} />)}</div></section>
          <section className="agent-report-section specialist-v3-audit-summary">
            <span className="plate-kicker">{t("Audit summary")}</span>
            <dl><div><dt>{t("Evidence sufficient")}</dt><dd>{document.audit_summary.verified}</dd></div><div><dt>{t("Evidence limited; certainty reduced")}</dt><dd>{document.audit_summary.insufficient}</dd></div><div><dt>{t("More evidence needed")}</dt><dd>{document.audit_summary.needs_more}</dd></div><div><dt>{t("Evidence conflict")}</dt><dd>{document.audit_summary.conflicted}</dd></div></dl>
          </section>
          <SourceDirectory locale={locale} sources={document.source_directory} citationLabelFor={citationLabelFor} />
        </>}
        <details className="agent-report-section report-v3-audit-details" data-export-audit="true">
          <summary>{t("Audit details")}</summary>
          <p><strong>{t("Canonical SHA")}</strong> <code>{report.integrity.canonical_sha256}</code></p>
          <h3>{t("Claim and citation index")}</h3>
          <ol>{document.claims.map(claim => <li key={claim.claim_id}><code>{claim.claim_id}</code><span>{t("Citations")}: {claim.citation_ids.map(citationId => citations.get(citationId)?.label).filter(label => label !== undefined).map(label => `[${label}]`).join(", ") || "—"}</span></li>)}</ol>
        </details>
        <ReportFooter locale={locale} canonicalSha={report.integrity.canonical_sha256} readOnly={Boolean(publicToken)} />
      </InstitutionalReportShell>
    </article>
  );
}
