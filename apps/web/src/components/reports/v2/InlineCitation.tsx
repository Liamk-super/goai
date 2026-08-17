import type { ReportCitationV2, SourceLocatorV2 } from "../../../lib/api-client";
import { CitationDetails } from "./CitationDetails";

export function InlineCitation({
  citation,
  source,
  evidenceHref,
}: {
  citation: ReportCitationV2;
  source?: SourceLocatorV2;
  evidenceHref: string;
}) {
  return (
    <details className="inline-citation">
      <summary aria-label={`Citation ${citation.label}`}>[{citation.label}]</summary>
      <CitationDetails citation={citation} source={source} evidenceHref={evidenceHref} />
    </details>
  );
}

