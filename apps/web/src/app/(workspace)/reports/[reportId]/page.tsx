"use client";

import { use, useEffect, useState } from "react";
import { browserApi, type ReportDisplay } from "../../../../lib/api-client";
import { SupervisorLayeredReport } from "../../../../components/reports/SupervisorLayeredReport";
import { useI18n } from "../../../../components/i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../../../../components/i18n/LocalizedErrorMessage";

export default function ReportPage({ params }: { params: Promise<{ reportId: string }> }) {
  const { t } = useI18n();
  const { reportId } = use(params);
  const [report, setReport] = useState<ReportDisplay>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    void browserApi().getReportForDisplay(reportId).then(setReport).catch(cause => {
      setError(cause instanceof Error ? cause.message : t("Report loading failed"));
    });
  }, [reportId, t]);

  if (report) return <SupervisorLayeredReport report={report} />;

  return (
    <main className="workspace-main">
      <section className="plate">
        <p className="plate-kicker">{t("Final report")}</p>
        <h1>{error ? t("The report is temporarily unavailable") : t("Preparing the report…")}</h1>
        {error && <LocalizedErrorMessage value={error} />}
      </section>
    </main>
  );
}
