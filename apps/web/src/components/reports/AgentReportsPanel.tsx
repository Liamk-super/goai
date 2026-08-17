"use client";

import { useEffect, useState } from "react";
import {
  browserApi,
  type AgentReportDetail,
  type AgentReportSummary,
} from "../../lib/api-client";
import { StatusPill } from "../shell/AppShell";
import { useI18n } from "../i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../i18n/LocalizedErrorMessage";
import { humanizeUserError } from "../../lib/user-report-formatter";

type JsonRecord = Record<string, unknown>;

function jsonRecord(value: unknown): JsonRecord | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : undefined;
}

function text(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function percent(value: unknown): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
}

function referenceText(value: unknown): string {
  const item = jsonRecord(value);
  return item ? text(item.ref) : text(value);
}

export function AgentReportDocument({ report }: { report: AgentReportDetail }) {
  const { t } = useI18n();
  let parsed: JsonRecord | undefined;
  if (report.format === "json") {
    try {
      parsed = jsonRecord(JSON.parse(report.content));
    } catch {
      parsed = undefined;
    }
  }

  if (!parsed) return <div className="agent-report-prose"><p>{report.content}</p></div>;

  const isDomain = parsed.schema_version === "DomainAgentReportViewV1";
  const isAudit = parsed.schema_version === "AgentAuditReportV1";
  return (
    <div className="agent-report-readable">
      <dl className="agent-report-metrics">
        <div><dt>{t("Status")}</dt><dd>{text(parsed.status, "AVAILABLE")}</dd></div>
        {isDomain && <div><dt>{t("Confidence")}</dt><dd>{percent(parsed.confidence)}</dd></div>}
        <div><dt>{t("Integrity")}</dt><dd>{report.projection_status === "LEGACY_SOURCE_PROJECTED" ? t("Verified source · durable readable projection") : t("Verified immutable artifact")}</dd></div>
        <div><dt>{t("Created")}</dt><dd>{new Date(report.created_at).toLocaleString()}</dd></div>
      </dl>

      {isDomain && (
        <>
          <section className="agent-report-section">
            <header><p className="plate-kicker">{t("Decision-relevant findings")}</p><h4>{t("Findings and evidence")}</h4></header>
            <div className="agent-report-findings">
              {list(parsed.findings).map((value, index) => {
                const finding = jsonRecord(value) ?? {};
                const limitations = list(finding.limitations);
                return (
                  <article key={text(finding.finding_id, String(index))} className="agent-report-finding">
                    <div className="agent-report-finding-head">
                      <span className="status-pill">{text(finding.grade, "NOT SCORED")}</span>
                      <span>{text(finding.dimension)} · {text(finding.subdimension)}</span>
                      <span>{t("Confidence")} {percent(finding.confidence)}</span>
                    </div>
                    <p className="agent-report-claim">{text(finding.claim)}</p>
                    {finding.hypothesis === true && <p className="unknown-banner">{t("Hypothesis · not verified fact")}</p>}
                    <dl className="agent-report-context">
                      <div><dt>{t("Region")}</dt><dd>{list(finding.region_scope).map(value => text(value)).join(" · ") || "—"}</dd></div>
                      <div><dt>{t("Evidence window")}</dt><dd>{text(finding.as_of)} → {text(finding.valid_until)}</dd></div>
                    </dl>
                    <div className="agent-report-evidence">
                      <strong>{t("Evidence references")}</strong>
                      <ul>{list(finding.evidence_refs).map((item, refIndex) => <li key={`${referenceText(item)}-${refIndex}`}>{referenceText(item)}</li>)}</ul>
                    </div>
                    {limitations.length > 0 && <div className="agent-report-risks"><strong>{t("Risks and limitations")}</strong><ul>{limitations.map((item, itemIndex) => <li key={itemIndex}>{text(item)}</li>)}</ul></div>}
                  </article>
                );
              })}
            </div>
          </section>
          <section className="agent-report-section agent-report-next">
            <p className="plate-kicker">{t("Next validation action")}</p>
            <p>{text(parsed.next_action, t("No additional action was supplied by this Agent."))}</p>
          </section>
          {list(parsed.limitations).length > 0 && <section className="agent-report-section agent-report-risks"><p className="plate-kicker">{t("Report-level limitations")}</p><ul>{list(parsed.limitations).map((item, index) => <li key={index}>{text(item)}</li>)}</ul></section>}
        </>
      )}

      {isAudit && (
        <section className="agent-report-section">
          <header><p className="plate-kicker">{t("Independent calibration")}</p><h4>{t("Evidence audit decisions")}</h4></header>
          <div className="agent-report-findings">
            {list(parsed.documents).map((value, index) => {
              const audit = jsonRecord(value) ?? {};
              const target = jsonRecord(audit.remediation_target);
              return (
                <article key={text(audit.finding_id, String(index))} className="agent-report-finding">
                  <div className="agent-report-finding-head">
                    <span className="status-pill">{text(audit.decision)}</span>
                    <span>{t("Freshness")} {text(audit.freshness_status)}</span>
                    <span>{t("Audit round {revision}", { revision: String(audit.audit_round ?? "—") })}</span>
                  </div>
                  <p className="agent-report-claim">{text(audit.reason)}</p>
                  <div className="agent-report-evidence"><strong>{t("Rules applied")}</strong><p>{list(audit.rule_ids).map(value => text(value)).join(" · ") || "—"}</p></div>
                  {list(audit.conflict_group_ids).length > 0 && <div className="agent-report-risks"><strong>{t("Conflicts")}</strong><p>{list(audit.conflict_group_ids).map(value => text(value)).join(" · ")}</p></div>}
                  {target && <div className="agent-report-risks"><strong>{t("Required remediation")}</strong><p>{text(target.question)} · {text(target.required_evidence)}</p></div>}
                </article>
              );
            })}
          </div>
        </section>
      )}

      {!isDomain && !isAudit && <section className="agent-report-section agent-report-prose"><p className="unknown-banner">{t("This artifact uses an unrecognized report schema. Its verified structure remains available below.")}</p></section>}

      <details className="agent-report-raw">
        <summary>{t("View raw verified structure")}</summary>
        <pre>{JSON.stringify(parsed, null, 2)}</pre>
      </details>
    </div>
  );
}

export function AgentReportsPanel({ runId }: { runId: string }) {
  const { locale, t } = useI18n();
  const [summaries, setSummaries] = useState<AgentReportSummary[]>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    let cancelled = false;
    setError(undefined);
    void browserApi().listAgentReportsForDisplay(runId).then(result => {
      if (!cancelled) setSummaries(result.reports);
    }).catch(cause => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : t("Agent report catalog loading failed"));
    });
    return () => { cancelled = true; };
  }, [runId, t]);

  return (
    <section className="plate plate-quiet agent-reports-panel" aria-labelledby="agent-reports-title">
      <header className="agent-reports-panel-header">
        <div>
          <p className="plate-kicker">{t("1+4 specialist reports")}</p>
          <h2 id="agent-reports-title">{t("View four specialist reports")}</h2>
        </div>
        <p className="g-meta">{t("Each detailed report opens in a new page for focused reading.")}</p>
      </header>
      {error && <LocalizedErrorMessage value={error} className="error-banner" />}
      {!summaries && !error && <p>{t("Reading durable report catalog…")}</p>}
      {summaries && (
        <ul className="agent-report-list">
          {summaries.map(summary => (
            <li key={summary.agent_code}>
              <span>
                <strong>{summary.title}</strong>
                <span className="bearing">
                  {summary.kind === "AUDIT" ? t("Audit round {revision}", { revision: summary.revision ?? "—" }) : t("Independent specialist report")}
                </span>
                {summary.failure_reason && <small>{humanizeUserError(summary.failure_reason, locale)}</small>}
              </span>
              <StatusPill value={summary.status} />
              {summary.status === "AVAILABLE" ? (
                <a
                  className="button secondary agent-report-link"
                  href={`/runs/${encodeURIComponent(runId)}/agent-reports/${summary.agent_code}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {t("Open detailed report")} <span aria-hidden="true">↗</span>
                </a>
              ) : (
                <span className="agent-report-unavailable" aria-live="polite">
                  <strong>{t("Report unavailable")}</strong>
                  <small>{summary.failure_reason ? humanizeUserError(summary.failure_reason, locale) : t("The execution needs attention before an independent report can be read.")}</small>
                  <small>{t("Execution source")}: {t("Durable Agent report catalog")}</small>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
