import type { ReportCitationV2, SourceLocatorV2 } from "../../../lib/api-client";
import { useI18n } from "../../i18n/LocaleProvider";

export function CitationDetails({
  citation,
  source,
  evidenceHref,
}: {
  citation: ReportCitationV2;
  source?: SourceLocatorV2;
  evidenceHref: string;
}) {
  const { t, status } = useI18n();
  return (
    <span className="citation-details">
      <strong>{source?.title ?? t("Evidence source")}</strong>
      <span>{source?.publisher ?? t("Uploaded project material")}</span>
      <span>{t("Audit status")}: {status(citation.audit_status)}</span>
      <span>{t("Retrieved")}: {source ? new Date(source.fetched_at).toLocaleDateString() : "—"}</span>
      {source?.canonical_url ? (
        <a href={source.canonical_url} target="_blank" rel="noopener noreferrer">{t("Open source")}</a>
      ) : (
        <a href={evidenceHref}>{t("View evidence")}</a>
      )}
    </span>
  );
}

