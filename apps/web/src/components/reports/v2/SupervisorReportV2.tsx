"use client";

import type { ReportClaimV2, ReportV2Projection } from "../../../lib/api-client";
import { presentReportText } from "../../../lib/report-copy";
import { useI18n } from "../../i18n/LocaleProvider";
import { AgentReportCards } from "./AgentReportCards";
import { InlineCitation } from "./InlineCitation";
import { ReportActions } from "./ReportActions";
import { ReportExportActions } from "./ReportExportActions";
import { ReportTopCard } from "./ReportTopCard";
import { PublicDemoShareAction } from "./PublicDemoShareAction";
import { ConfidentialCover, InstitutionalReportShell, ReportFooter } from "../institutional/InstitutionalReport";

export function SupervisorReportV2({
  report,
  readOnly = false,
  runHref,
  agentHrefFor,
  evidenceHrefFor,
  publicToken,
}: {
  report: ReportV2Projection;
  readOnly?: boolean;
  runHref?: string;
  agentHrefFor?: (agentCode: ReportV2Projection["document"]["agent_report_cards"][number]["agent_code"]) => string;
  evidenceHrefFor?: (evidenceId: string) => string;
  publicToken?: string;
}) {
  const { locale, t, status } = useI18n();
  const document = report.document;
  const claims = new Map(document.claims.map(claim => [claim.claim_id, claim]));
  const citations = new Map(document.citations.map(citation => [citation.citation_id, citation]));
  const citedSourceIds = new Set(document.citations.map(citation => citation.source_locator_id).filter(Boolean));
  const sources = new Map(document.source_directory.map(source => [source.source_locator_id, source]));
  const visibleSources = Array.from(document.source_directory
    .slice()
    .sort((left, right) => Number(citedSourceIds.has(right.source_locator_id)) - Number(citedSourceIds.has(left.source_locator_id)))
    .reduce((unique, source) => {
      const key = source.canonical_url?.toLowerCase() ?? `${source.source_kind}:${source.title.toLowerCase()}`;
      if (!unique.has(key)) unique.set(key, source);
      return unique;
    }, new Map<string, typeof document.source_directory[number]>()).values());
  const sourcePublisher = (source: typeof document.source_directory[number]) => {
    if (source.publisher) return source.publisher;
    if (source.canonical_url) {
      try { return new URL(source.canonical_url).hostname.replace(/^www\./u, ""); } catch { return t("Public web source"); }
    }
    return t("Uploaded project material");
  };
  const renderInlineCitations = (claim: ReportClaimV2) => claim.citation_ids.map(citationId => {
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
  });
  const renderClaim = (claim: ReportClaimV2) => (
    <article className="plate plate-quiet report-v22-claim" key={claim.claim_id} data-status={claim.status}>
      <header>
        <span className="status-pill">{status(claim.status)}</span>
        <span className="bearing">{status(claim.decision_relevance)}</span>
        <span className="bearing">{status(claim.section)}</span>
      </header>
      <div className="report-v22-claim-copy">
        {presentReportText(locale, claim.text)}{" "}{renderInlineCitations(claim)}
      </div>
      {claim.status === "PENDING_VALIDATION" && <small>{t("Pending validation · excluded from scoring and the main recommendation")}</small>}
    </article>
  );
  const summaryClaim = claims.get(document.summary_claim_id);
  const summaryDetailIds = new Set([...document.highlights, ...document.critical_issues]);
  const detailedClaims = document.claims.filter(
    claim => claim.claim_id !== document.summary_claim_id && !summaryDetailIds.has(claim.claim_id),
  );

  return (
    <main className="workspace-main supervisor-report-page report-v22-page institutional-report-page" data-report-ready="true">
      <InstitutionalReportShell className="institutional-v2-report">
      <ConfidentialCover
        locale={locale}
        title={document.product_title}
        reportId={document.report_id}
        runId={document.run_id}
        createdAt={report.projection.created_at}
        canonicalSha={report.integrity.canonical_sha256}
        readOnly={readOnly}
      />
      <header className="student-report-heading">
        <div><span className="bearing">{t("Prediction target")} {document.product_title}</span><h1>{t("Prediction report")}</h1></div>
        <a href={runHref ?? `/runs/${document.run_id}`}>← {t("Return to prediction")}</a>
      </header>

      <ReportTopCard document={document} />

      <ReportExportActions reportId={document.report_id} allowPackage publicToken={publicToken} />
      {!readOnly && <PublicDemoShareAction reportId={document.report_id} />}

      <section className="report-v22-section" aria-labelledby="report-summary-title">
        <span className="plate-kicker">{t("Final conclusion")}</span>
        <h2 id="report-summary-title">{summaryClaim && presentReportText(locale, summaryClaim.text)} {summaryClaim && renderInlineCitations(summaryClaim)}</h2>
        <div className="report-v22-claim-grid">
          {[...document.highlights, ...document.critical_issues]
            .filter((claimId, index, values) => values.indexOf(claimId) === index)
            .map(claimId => claims.get(claimId))
            .filter((claim): claim is ReportClaimV2 => Boolean(claim))
            .map(renderClaim)}
        </div>
      </section>

      {detailedClaims.length > 0 && (
        <section className="report-v22-section" aria-labelledby="report-detail-title">
          <span className="plate-kicker">{t("Key judgments")}</span>
          <h2 id="report-detail-title">{t("Detailed analysis")}</h2>
          <div className="report-v22-claim-grid">{detailedClaims.map(renderClaim)}</div>
        </section>
      )}

      <section className="plate report-v22-dimensions" aria-labelledby="index-dimensions-title">
        <span className="plate-kicker">{t("Index composition")}</span>
        <h2 id="index-dimensions-title">{t("How the hit potential index is composed")}</h2>
        <ul>
          <li><strong>{t("user_value")}</strong></li>
          <li><strong>{t("product_capability")}</strong></li>
          <li><strong>{t("investment_potential")}</strong></li>
          <li><strong>{t("evidence_quality")}</strong></li>
        </ul>
        <p className="g-meta">{t("Only audited evidence can enter the index; confidence and evidence completeness are calculated separately.")}</p>
      </section>

      {document.comparison?.status === "COMPARABLE" && document.comparison.dimension_deltas && (
        <section className="plate report-v22-dimensions" aria-labelledby="dimension-change-title">
          <span className="plate-kicker">{t("Why the index changed")}</span>
          <h2 id="dimension-change-title">{t("Dimension additions and deductions")}</h2>
          <ul>{document.comparison.dimension_deltas.map(item => (
            <li key={item.dimension}>
              <strong>{t(item.dimension)}</strong><span>{Math.round(item.before)} → {Math.round(item.after)}</span>
              <span data-tone={item.delta >= 0 ? "positive" : "attention"}>{item.delta >= 0 ? "+" : ""}{Math.round(item.delta)}</span>
            </li>
          ))}</ul>
          <p className="g-meta">{t("These values are determined by the scoring profile and cannot be edited in the report.")}</p>
        </section>
      )}

      <ReportActions actions={document.actions} />

      <section className="plate report-v22-sources" aria-labelledby="source-directory-title">
        <span className="plate-kicker">{t("Sources")}</span>
        <h2 id="source-directory-title">{t("Source directory")}</h2>
        <ol>{visibleSources.map(source => (
          <li key={source.source_locator_id}>
            <span>[{document.citations.find(item => item.source_locator_id === source.source_locator_id)?.label ?? "—"}]</span>
            <div><strong>{source.title}</strong><small>{sourcePublisher(source)} · {new Date(source.published_at ?? source.fetched_at).toLocaleDateString()}</small></div>
            {source.canonical_url && <a href={source.canonical_url} target="_blank" rel="noopener noreferrer">{t("Open source")}</a>}
          </li>
        ))}</ol>
      </section>

      <AgentReportCards
        document={document}
        hrefFor={agentHrefFor ?? (agentCode => `/runs/${encodeURIComponent(document.run_id)}/agent-reports/${agentCode}`)}
      />

      <p className="supervisor-report-footer-actions">
        {!readOnly && <a className="button" href={`/projects/${document.project_id}/new-evaluation`}>{t("Submit a new version and re-evaluate")}</a>}
        <a className={readOnly ? "button" : "button secondary"} href="/">{t("Return to wheel home")}</a>
      </p>
      <ReportFooter locale={locale} canonicalSha={report.integrity.canonical_sha256} readOnly={readOnly} />
      </InstitutionalReportShell>
    </main>
  );
}
