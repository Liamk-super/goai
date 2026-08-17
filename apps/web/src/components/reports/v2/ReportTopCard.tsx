import type { SupervisorReportDocumentV2, SupervisorReportDocumentV3 } from "../../../lib/api-client";
import { useI18n } from "../../i18n/LocaleProvider";

const recommendationKeys = {
  PROCEED: "Proceed to the next stage",
  VALIDATE_FURTHER: "Validate further",
  ADJUST: "Adjust before proceeding",
  PAUSE: "Pause further investment",
} as const;

export function ReportTopCard({ document }: { document: SupervisorReportDocumentV2 | SupervisorReportDocumentV3 }) {
  const { t, status } = useI18n();
  const comparison = document.comparison;
  return (
    <section className="plate report-v22-top-card" aria-labelledby="potential-index-title">
      <div className="report-v22-index">
        <span className="plate-kicker" id="potential-index-title">{t("Hit potential index")}</span>
        <strong>{Math.round(document.top_card.potential_index)}</strong><span>/ 100</span>
      </div>
      <dl className="report-v22-top-facts">
        <div><dt>{t("Stage")}</dt><dd>{status(document.top_card.stage)}</dd></div>
        {comparison?.status === "COMPARABLE" && (
          <div className="report-v22-comparison">
            <dt>{t("Compared with last time")}</dt>
            <dd>
              {Math.round(comparison.index_before ?? 0)} → {Math.round(comparison.index_after ?? 0)}
              <span data-tone={(comparison.index_delta ?? 0) >= 0 ? "positive" : "attention"}>
                {(comparison.index_delta ?? 0) >= 0 ? "+" : ""}{Math.round(comparison.index_delta ?? 0)}
              </span>
            </dd>
          </div>
        )}
        {comparison?.status === "STANDARD_CHANGED" && (
          <div className="report-v22-comparison" data-tone="attention">
            <dt>{t("Compared with last time")}</dt>
            <dd>{t("The evaluation standard changed; no index difference is shown.")}</dd>
          </div>
        )}
        <div className="report-v22-confidence">
          <dt>{t("Confidence")}</dt>
          <dd><strong>{status(document.top_card.confidence_band)}</strong> · {Math.round(document.confidence_breakdown.score * 100)}%</dd>
        </div>
        <div className="report-v22-coverage">
          <dt>{t(document.schema_version === "3.0" ? "Evidence coverage" : "Evidence completeness")}</dt>
          <dd>{Math.round(document.top_card.evidence_coverage * 100)}% · {t("Supporting context, calculated separately from confidence")}</dd>
        </div>
        <div><dt>{t("Action recommendation")}</dt><dd>{t(recommendationKeys[document.top_card.recommendation])}</dd></div>
      </dl>
    </section>
  );
}
