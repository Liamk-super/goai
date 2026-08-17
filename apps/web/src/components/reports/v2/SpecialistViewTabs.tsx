import { useI18n } from "../../i18n/LocaleProvider";

export type SpecialistView = "summary" | "full";

export function SpecialistViewTabs({ view, onChange }: { view: SpecialistView; onChange: (view: SpecialistView) => void }) {
  const { t } = useI18n();
  return (
    <div className="specialist-view-tabs" role="tablist" aria-label={t("Report view") }>
      <button type="button" role="tab" data-report-view="summary" aria-selected={view === "summary"} onClick={() => onChange("summary")}>
        {t("Summary")}
      </button>
      <button type="button" role="tab" data-report-view="full" aria-selected={view === "full"} onClick={() => onChange("full")}>
        {t("Full report")}
      </button>
    </div>
  );
}
