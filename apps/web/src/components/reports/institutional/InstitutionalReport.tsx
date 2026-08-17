import type { ReactNode } from "react";

import type {
  ReportActionV2,
  SourceLocatorV2,
  SupervisorReportDocumentV2,
  SupervisorReportDocumentV3,
} from "../../../lib/api-client";
import { translate } from "../../../lib/i18n";
import { presentReportText } from "../../../lib/report-copy";

type Locale = "zh-CN" | "en";

function localized(locale: Locale, key: string) {
  return translate(locale, key);
}

function compactId(value: string) {
  return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

export function InstitutionalReportShell({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`institutional-report ${className}`.trim()}>{children}</div>;
}

export function ConfidentialCover({
  locale,
  title,
  reportId,
  runId,
  createdAt,
  canonicalSha,
  kind = "SUPERVISOR",
  readOnly = false,
}: {
  locale: Locale;
  title: string;
  reportId: string;
  runId: string;
  createdAt: string;
  canonicalSha: string;
  kind?: "SUPERVISOR" | "SPECIALIST";
  readOnly?: boolean;
}) {
  const reportType = kind === "SUPERVISOR"
    ? localized(locale, "Investment decision diligence report")
    : localized(locale, "Specialist diligence report");
  return (
    <header className="institutional-cover">
      <div className="institutional-cover-seal" aria-hidden="true">LS</div>
      <div className="institutional-cover-copy">
        <p>{readOnly ? localized(locale, "Read-only share · run scoped") : localized(locale, "Confidential · internal decision use only")}</p>
        <h1>{title}</h1>
        <span>{reportType}</span>
      </div>
      <dl className="institutional-cover-meta">
        <div><dt>{localized(locale, "Report reference")}</dt><dd>{compactId(reportId)}</dd></div>
        <div><dt>{localized(locale, "Run")}</dt><dd>{compactId(runId)}</dd></div>
        <div><dt>{localized(locale, "Issued")}</dt><dd>{new Date(createdAt).toLocaleDateString(locale)}</dd></div>
        <div><dt>{localized(locale, "Canonical")}</dt><dd>{compactId(canonicalSha)}</dd></div>
      </dl>
    </header>
  );
}

export function DecisionCard({
  locale,
  index,
  recommendation,
  stage,
  confidence,
  coverage,
}: {
  locale: Locale;
  index: number;
  recommendation: string;
  stage: string;
  confidence: string;
  coverage: number;
}) {
  return (
    <section className="institutional-decision-card" aria-label={localized(locale, "Decision summary")}>
      <div className="institutional-score"><span>{localized(locale, "Conditional index")}</span><strong>{Math.round(index)}</strong><small>/ 100</small></div>
      <dl>
        <div><dt>{localized(locale, "Current gate")}</dt><dd>{recommendation}</dd></div>
        <div><dt>{localized(locale, "Stage")}</dt><dd>{stage}</dd></div>
        <div><dt>{localized(locale, "Confidence")}</dt><dd>{confidence}</dd></div>
        <div><dt>{localized(locale, "Evidence coverage")}</dt><dd>{Math.round(coverage * 100)}%</dd></div>
      </dl>
    </section>
  );
}

export function EvidenceBadgeRow({
  locale,
  covered,
  required,
  confidence,
  profileRef,
}: {
  locale: Locale;
  covered: number;
  required: number;
  confidence: number;
  profileRef: string;
}) {
  return (
    <section className="institutional-evidence-badges" aria-label={localized(locale, "Evidence disclosure")}>
      <span>{localized(locale, "Covered dimensions")} <b>{covered}/{required}</b></span>
      <span>{localized(locale, "Confidence")} <b>{Math.round(confidence * 100)}%</b></span>
      <span>{localized(locale, "Profile")} <b>{profileRef}</b></span>
      <span>{localized(locale, "Scoring note")} <b>{localized(locale, "not a success probability")}</b></span>
    </section>
  );
}

export function VersionDeltaPanel({
  locale,
  comparison,
  claimText,
}: {
  locale: Locale;
  comparison: SupervisorReportDocumentV2["comparison"] | SupervisorReportDocumentV3["comparison"];
  claimText: (claimId: string) => ReactNode;
}) {
  if (!comparison) return null;
  if (comparison.status === "STANDARD_CHANGED") {
    return (
      <section className="institutional-delta institutional-delta-caution">
        <span>{localized(locale, "Version delta")}</span>
        <h2>{localized(locale, "Evaluation standard changed; numeric comparison is withheld")}</h2>
      </section>
    );
  }
  return (
    <section className="institutional-delta" aria-labelledby="institutional-delta-title">
      <span>{localized(locale, "Version delta")}</span>
      <h2 id="institutional-delta-title">{localized(locale, "Change against the bound baseline")}</h2>
      <div className="institutional-delta-score">
        <strong>{Math.round(comparison.index_before ?? 0)} → {Math.round(comparison.index_after ?? 0)}</strong>
        <small data-tone={(comparison.index_delta ?? 0) >= 0 ? "positive" : "attention"}>{(comparison.index_delta ?? 0) >= 0 ? "+" : ""}{Math.round(comparison.index_delta ?? 0)}</small>
      </div>
      <div className="institutional-delta-columns">
        <div><h3>{localized(locale, "Resolved actions")}</h3><ul>{comparison.resolved_issues.map(item => <li key={item}>{item}</li>)}</ul></div>
        <div><h3>{localized(locale, "Unresolved items")}</h3><ul>{comparison.unchanged_issues.map(item => <li key={item}>{item}</li>)}</ul></div>
        <div><h3>{localized(locale, "New risks")}</h3><ul>{comparison.new_risks.map(item => <li key={item}>{item}</li>)}</ul></div>
      </div>
      {comparison.change_reason_claim_ids.length > 0 && <div className="institutional-delta-claims">{comparison.change_reason_claim_ids.map(claimId => <div key={claimId}>{claimText(claimId)}</div>)}</div>}
    </section>
  );
}

export function ScoreDimensionTable({
  locale,
  document,
  labelFor,
}: {
  locale: Locale;
  document: SupervisorReportDocumentV3;
  labelFor: (key: "user_value" | "product_capability" | "investment_potential" | "evidence_quality") => string;
}) {
  const keys = ["user_value", "product_capability", "investment_potential", "evidence_quality"] as const;
  return (
    <section className="institutional-table-section">
      <div className="institutional-section-heading"><span>02</span><div><p>{localized(locale, "Score explanation")}</p><h2>{localized(locale, "Four-dimensional conditional score")}</h2></div></div>
      <div className="institutional-table-wrap">
        <table className="institutional-table">
          <thead><tr><th>{localized(locale, "Dimension")}</th><th>{localized(locale, "Index")}</th><th>{localized(locale, "Evidence strength")}</th><th>{localized(locale, "Scoring boundary")}</th></tr></thead>
          <tbody>{keys.map(key => {
            const dimension = document.dimension_scores[key];
            return <tr key={key}><th scope="row">{labelFor(key)}</th><td>{dimension.value === null ? localized(locale, "Pending") : Math.round(dimension.value)}</td><td>{dimension.strength} · {dimension.evidence_level}</td><td>{dimension.pending_validation_claim_ids.length > 0 ? localized(locale, "Pending items excluded") : localized(locale, "Audited evidence only")}</td></tr>;
          })}</tbody>
        </table>
      </div>
    </section>
  );
}

export function GateBanner({ locale, recommendation }: { locale: Locale; recommendation: string }) {
  return <aside className="institutional-gate"><span>{localized(locale, "Investment gate")}</span><strong>{recommendation}</strong><p>{localized(locale, "The index does not override the gate; pending claims are not verified conclusions.")}</p></aside>;
}

export function DueDiligenceTable({
  locale,
  rows,
}: {
  locale: Locale;
  rows: Array<{ label: string; value: ReactNode; detail?: ReactNode }>;
}) {
  return <div className="institutional-table-wrap"><table className="institutional-table institutional-due-diligence-table"><thead><tr><th>{localized(locale, "Review item")}</th><th>{localized(locale, "Canonical projection")}</th><th>{localized(locale, "Review note")}</th></tr></thead><tbody>{rows.map(row => <tr key={row.label}><th scope="row">{row.label}</th><td>{row.value}</td><td>{row.detail ?? "—"}</td></tr>)}</tbody></table></div>;
}

export function ActionCard({ locale, action, priority }: { locale: Locale; action: ReportActionV2; priority: "P0" | "P1" | "PX" }) {
  return (
    <article className="institutional-action-card" data-priority={priority}>
      <header><span>{priority}</span><div><h3>{presentReportText(locale, action.title)}</h3><p>{localized(locale, "Owner")}: {presentReportText(locale, action.owner)} · {localized(locale, "Due")}: {action.deadline_days}{localized(locale, " days")}</p></div></header>
      <dl><div><dt>{localized(locale, "Pass criteria")}</dt><dd>{action.success_criteria.map(value => presentReportText(locale, value)).join(" · ") || "—"}</dd></div><div><dt>{localized(locale, "Failure signal")}</dt><dd>{action.failure_triggers.map(value => presentReportText(locale, value)).join(" · ") || "—"}</dd></div><div><dt>{localized(locale, "Required evidence")}</dt><dd>{action.required_evidence.map(value => presentReportText(locale, value)).join(" · ") || "—"}</dd></div></dl>
    </article>
  );
}

export function RiskCallout({ locale, title, children, tone = "attention" }: { locale: Locale; title: string; children: ReactNode; tone?: "attention" | "neutral" }) {
  return <aside className="institutional-risk-callout" data-tone={tone}><span>{tone === "attention" ? localized(locale, "Attention") : localized(locale, "Review note")}</span><h3>{title}</h3><div>{children}</div></aside>;
}

export function SourceDirectory({ locale, sources, citationLabelFor }: { locale: Locale; sources: SourceLocatorV2[]; citationLabelFor: (sourceId: string) => string }) {
  return (
    <section className="institutional-sources" aria-label={localized(locale, "Source directory")}>
      <div className="institutional-section-heading"><span>06</span><div><p>{localized(locale, "Evidence directory")}</p><h2>{localized(locale, "Sources and traceability")}</h2></div></div>
      <ol>{sources.map(source => <li key={source.source_locator_id}><span>{citationLabelFor(source.source_locator_id)}</span><div><strong>{source.title}</strong><small>{source.publisher ?? localized(locale, "Uploaded project material")}</small></div>{source.canonical_url && <a href={source.canonical_url} target="_blank" rel="noopener noreferrer">{localized(locale, "Open source")}</a>}</li>)}</ol>
    </section>
  );
}

export function ReportFooter({ locale, canonicalSha, readOnly }: { locale: Locale; canonicalSha: string; readOnly: boolean }) {
  return <footer className="institutional-report-footer"><span>{readOnly ? localized(locale, "Read-only, run-scoped report projection") : localized(locale, "Deterministic projection of the canonical report")}</span><code>SHA-256 {compactId(canonicalSha)}</code></footer>;
}
