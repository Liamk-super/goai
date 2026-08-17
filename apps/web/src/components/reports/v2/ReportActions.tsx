import type { ReportActionV2 } from "../../../lib/api-client";
import { presentReportText } from "../../../lib/report-copy";
import { useI18n } from "../../i18n/LocaleProvider";

export function ReportActions({ actions }: { actions: ReportActionV2[] }) {
  const { locale, t } = useI18n();
  return (
    <section className="plate supervisor-action-plate report-v22-actions">
      <span className="plate-kicker">{t("Action plan")}</span>
      <h2>{t("Prioritize these next")}</h2>
      <ol>
        {actions.map((action, index) => (
          <li key={action.action_id}>
            <span>0{index + 1}</span>
            <div>
              <h3>{presentReportText(locale, action.title)}</h3>
              <p>{t("Owner")}: {presentReportText(locale, action.owner)} · {t("Complete within {days} days", { days: action.deadline_days })}</p>
              <details>
                <summary>{t("Success, failure, and required evidence")}</summary>
                <dl>
                  <div><dt>{t("Success criteria")}</dt><dd>{action.success_criteria.map(value => presentReportText(locale, value)).join(" · ")}</dd></div>
                  <div><dt>{t("Failure triggers")}</dt><dd>{action.failure_triggers.map(value => presentReportText(locale, value)).join(" · ")}</dd></div>
                  <div><dt>{t("Required evidence")}</dt><dd>{action.required_evidence.map(value => presentReportText(locale, value)).join(" · ")}</dd></div>
                </dl>
              </details>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
