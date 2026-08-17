import type { SupervisorReportDocumentV2, SupervisorReportDocumentV3 } from "../../../lib/api-client";
import { useI18n } from "../../i18n/LocaleProvider";

export function AgentReportCards({
  document,
  hrefFor,
}: {
  document: SupervisorReportDocumentV2 | SupervisorReportDocumentV3;
  hrefFor: (agentCode: SupervisorReportDocumentV2["agent_report_cards"][number]["agent_code"]) => string;
}) {
  const { t } = useI18n();
  return (
    <section className="plate plate-quiet report-v22-agent-reports" id="agent-reports" aria-labelledby="agent-reports-v22-title">
      <header>
        <span className="plate-kicker">{t("1+4 specialist reports")}</span>
        <h2 id="agent-reports-v22-title">{t("Open the four detailed specialist reports")}</h2>
      </header>
      <ul>
        {document.agent_report_cards.map(card => (
          <li key={card.agent_code}>
            <a href={hrefFor(card.agent_code)} target="_blank" rel="noopener noreferrer">
              <span><strong>{card.title}</strong><small>{t("Open detailed report")}</small></span>
              <span aria-hidden="true">↗</span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
