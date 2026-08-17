"use client";

import type { ReportClaimV2, ReportV3Projection } from "../../../lib/api-client";
import { visibleReportPriorities } from "../../../lib/report-v3-presentation";
import { presentReportText } from "../../../lib/report-copy";
import { useI18n } from "../../i18n/LocaleProvider";
import { AgentReportCards } from "../v2/AgentReportCards";
import { InlineCitation } from "../v2/InlineCitation";
import { PublicDemoShareAction } from "../v2/PublicDemoShareAction";
import { ReportActions } from "../v2/ReportActions";
import { ReportExportActions } from "../v2/ReportExportActions";
import { ReportTopCard } from "../v2/ReportTopCard";
import {
  ActionCard,
  ConfidentialCover,
  DecisionCard,
  EvidenceBadgeRow,
  GateBanner,
  InstitutionalReportShell,
  ReportFooter,
  RiskCallout,
  ScoreDimensionTable,
  SourceDirectory,
  VersionDeltaPanel,
} from "../institutional/InstitutionalReport";

const dimensionKeys = ["user_value", "product_capability", "investment_potential", "evidence_quality"] as const;

export function SupervisorReportV3({
  report,
  readOnly = false,
  runHref,
  agentHrefFor,
  evidenceHrefFor,
  publicToken,
}: {
  report: ReportV3Projection;
  readOnly?: boolean;
  runHref?: string;
  agentHrefFor?: (agentCode: ReportV3Projection["document"]["agent_report_cards"][number]["agent_code"]) => string;
  evidenceHrefFor?: (evidenceId: string) => string;
  publicToken?: string;
}) {
  const { locale, t, status } = useI18n();
  const document = report.document;
  const claims = new Map(document.claims.map(claim => [claim.claim_id, claim]));
  const citations = new Map(document.citations.map(citation => [citation.citation_id, citation]));
  const sources = new Map(document.source_directory.map(source => [source.source_locator_id, source]));
  const priorities = visibleReportPriorities(document.issue_priorities, document.actions);
  const summaryClaim = claims.get(document.summary_claim_id);

  const renderCitations = (claim: ReportClaimV2) => claim.citation_ids.map(citationId => {
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
      <header><span className="status-pill">{status(claim.status)}</span><span className="bearing">{status(claim.decision_relevance)}</span></header>
      <div className="report-v22-claim-copy">{presentReportText(locale, claim.text)} {renderCitations(claim)}</div>
      {claim.status === "PENDING_VALIDATION" && <small>{t("Pending validation · excluded from scoring and the main recommendation")}</small>}
    </article>
  );
  const renderClaimSection = (title: string, claimIds: string[]) => {
    const sectionClaims = claimIds.map(claimId => claims.get(claimId)).filter((claim): claim is ReportClaimV2 => Boolean(claim));
    return (
      <section className="report-v3-full-section" key={title}>
        <h3>{t(title)}</h3>
        {sectionClaims.length > 0
          ? <div className="report-v22-claim-grid">{sectionClaims.map(renderClaim)}</div>
          : <p className="truthful-empty-state">{t("No evidence-backed findings are available in this section yet")}</p>}
      </section>
    );
  };

  const citationLabelFor = (sourceId: string) => `[${document.citations.find(item => item.source_locator_id === sourceId)?.label ?? "—"}]`;
  const priorityFor = (index: number): "P0" | "P1" | "PX" => index === 0 ? "P0" : index === 1 ? "P1" : "PX";

  return (
    <main className="workspace-main supervisor-report-page report-v22-page report-v3-page institutional-report-page" data-report-ready="true" data-canonical-sha256={report.integrity.canonical_sha256}>
      <InstitutionalReportShell>
        <ConfidentialCover
          locale={locale}
          title={document.product_title}
          reportId={document.report_id}
          runId={document.run_id}
          createdAt={report.projection.created_at}
          canonicalSha={report.integrity.canonical_sha256}
          readOnly={readOnly}
        />
        <header className="student-report-heading institutional-report-navigation">
          <div><span className="bearing">{t("Prediction target")} {document.product_title}</span><h2>{t("Prediction report")}</h2></div>
          <a href={runHref ?? `/runs/${document.run_id}`}>← {t("Return to prediction")}</a>
        </header>

        <DecisionCard
          locale={locale}
          index={document.top_card.potential_index}
          recommendation={t(document.top_card.recommendation)}
          stage={status(document.top_card.stage)}
          confidence={status(document.top_card.confidence_band)}
          coverage={document.top_card.evidence_coverage}
        />
        <ReportTopCard document={document} />
        <EvidenceBadgeRow
          locale={locale}
          covered={document.evidence_coverage_profile.covered_dimensions}
          required={document.evidence_coverage_profile.required_dimensions}
          confidence={document.confidence_breakdown.score}
          profileRef={document.confidence_breakdown.profile_ref}
        />
        <div className="institutional-report-actions">
          <ReportExportActions reportId={document.report_id} allowPackage publicToken={publicToken} />
          {!readOnly && <PublicDemoShareAction reportId={document.report_id} />}
        </div>

        <section className="report-v22-section report-v3-first-screen institutional-decision-section" aria-labelledby="report-v3-summary">
          <div className="institutional-section-heading"><span>01</span><div><p>{t("Final conclusion")}</p><h2 id="report-v3-summary">{t("Comprehensive conclusion")}</h2></div></div>
          {summaryClaim
            ? <div className="report-v3-summary-copy">{presentReportText(locale, summaryClaim.text)} {renderCitations(summaryClaim)}</div>
            : <p className="truthful-empty-state">{t("The comprehensive conclusion is pending auditable evidence")}</p>}
          <GateBanner locale={locale} recommendation={t(document.top_card.recommendation)} />
          <div className="report-v3-priority-grid">
            {priorities.issues.map(issue => {
              const claim = claims.get(issue.claim_id);
              return claim && (
                <article className="plate plate-quiet report-v3-priority" key={issue.claim_id} data-priority={issue.priority}>
                  <header><strong>{issue.priority}</strong><span>{issue.decision_impact}</span></header>
                  <div className="report-v3-priority-copy">{presentReportText(locale, claim.text)} {renderCitations(claim)}</div>
                </article>
              );
            })}
          </div>
          {priorities.issues.length === 0 && <p className="truthful-empty-state">{t("No evidence-backed priority issue is available yet")}</p>}
        </section>

        <VersionDeltaPanel
          locale={locale}
          comparison={document.comparison}
          claimText={claimId => {
            const claim = claims.get(claimId);
            return claim ? <>{presentReportText(locale, claim.text)} {renderCitations(claim)}</> : <span>—</span>;
          }}
        />

        <ScoreDimensionTable locale={locale} document={document} labelFor={key => t(key)} />
        <section className="plate report-v3-dimensions institutional-driver-notes" aria-labelledby="report-v3-dimensions">
          <h2 id="report-v3-dimensions">{t("Four-dimensional score and evidence drivers")}</h2>
          <div className="report-v3-dimension-grid">
            {dimensionKeys.map(key => {
              const dimension = document.dimension_scores[key];
              const drivers = [
                ...dimension.positive_driver_claim_ids.map(claimId => ({ claimId, tone: "positive", prefix: "+" })),
                ...dimension.negative_driver_claim_ids.map(claimId => ({ claimId, tone: "attention", prefix: "−" })),
                ...dimension.pending_validation_claim_ids.map(claimId => ({ claimId, tone: "pending", prefix: "?" })),
              ];
              return (
                <article className="report-v3-dimension" key={key}>
                  <header><span>{t(key)}</span><strong>{dimension.value === null ? t("Pending validation") : Math.round(dimension.value)}</strong></header>
                  <small>{status(dimension.strength)} · {status(dimension.evidence_level)}</small>
                  <ul>{drivers.map(driver => {
                    const claim = claims.get(driver.claimId);
                    return claim && <li key={`${key}-${driver.tone}-${driver.claimId}`} data-tone={driver.tone}><b>{driver.prefix}</b><div>{presentReportText(locale, claim.text)} {renderCitations(claim)}</div></li>;
                  })}</ul>
                </article>
              );
            })}
          </div>
        </section>

        <details className="plate plate-quiet report-v3-evidence-explainer">
        <summary>{t("Why this judgment")}</summary>
        <div className="report-v3-evidence-facts">
          <p><strong>{t("Evidence coverage")}</strong> {document.evidence_coverage_profile.covered_dimensions}/{document.evidence_coverage_profile.required_dimensions}</p>
          <p>{presentReportText(locale, document.evidence_coverage_profile.quality_note)}</p>
          <p>{presentReportText(locale, document.evidence_coverage_profile.independent_support_note)}</p>
          <p>{t("Confidence")}: {Math.round(document.confidence_breakdown.score * 100)}% · {status(document.confidence_breakdown.band)}</p>
        </div>
        </details>

        <section className="institutional-action-section" aria-label={t("Action plan")}>
          <div className="institutional-section-heading"><span>03</span><div><p>{t("Action plan")}</p><h2>{t("Prioritize these next")}</h2></div></div>
          <div className="institutional-action-grid">{priorities.actions.map((action, index) => <ActionCard key={action.action_id} locale={locale} action={action} priority={priorityFor(index)} />)}</div>
          {priorities.actions.length === 0 && <p className="truthful-empty-state">{t("No evidence-backed priority issue is available yet")}</p>}
        </section>

        <RiskCallout locale={locale} title={t("Pause conditions and next review rule")}>
          <p>{t("Only the canonical action evidence and an auditor-approved reassessment can close this gate. Pending validation and uncertain external side effects remain attention states.")}</p>
        </RiskCallout>

        <details className="plate report-v3-full-report">
        <summary>{t("Read the full project lead report")}</summary>
        {renderClaimSection("Highlights", document.highlights)}
        {renderClaimSection("Critical issues", document.critical_issues)}
        {renderClaimSection("Target user judgment", document.role_summaries.user)}
        {renderClaimSection("Product judgment", document.role_summaries.product)}
        {renderClaimSection("Investment judgment", document.role_summaries.investment)}
        {renderClaimSection("Cross-domain synthesis and uncertainty", document.cross_domain_claims)}
          {document.actions.length > 3 && <ReportActions actions={document.actions.slice(3)} />}
        </details>

        <SourceDirectory locale={locale} sources={document.source_directory} citationLabelFor={citationLabelFor} />

        <details className="plate plate-quiet report-v3-audit-details" data-export-audit="true">
        <summary>{t("Audit details")}</summary>
        <p><strong>{t("Canonical SHA")}</strong> <code>{report.integrity.canonical_sha256}</code></p>
        <h3>{t("Claim and citation index")}</h3>
        <ol>{document.claims.map(claim => <li key={claim.claim_id}><code>{claim.claim_id}</code><span>{t("Citations")}: {claim.citation_ids.map(citationId => citations.get(citationId)?.label).filter(label => label !== undefined).map(label => `[${label}]`).join(", ") || "—"}</span></li>)}</ol>
        </details>

        <AgentReportCards document={document} hrefFor={agentHrefFor ?? (agentCode => `/runs/${encodeURIComponent(document.run_id)}/agent-reports/${agentCode}`)} />
        <p className="supervisor-report-footer-actions">
          {!readOnly && <a className="button" href={`/projects/${document.project_id}/new-evaluation`}>{t("Submit a new version and re-evaluate")}</a>}
          <a className={readOnly ? "button" : "button secondary"} href="/">{t("Return to wheel home")}</a>
        </p>
        <ReportFooter locale={locale} canonicalSha={report.integrity.canonical_sha256} readOnly={readOnly} />
      </InstitutionalReportShell>
    </main>
  );
}
