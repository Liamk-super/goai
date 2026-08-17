"use client";

import { formatUserVisibleError } from "../../lib/user-report-formatter";
import { useI18n } from "./LocaleProvider";

export function LocalizedErrorMessage({
  value,
  className,
}: {
  value: string;
  className?: string;
}) {
  const { locale, t } = useI18n();
  const error = formatUserVisibleError(value, locale);
  return (
    <div role="alert" className={["localized-error", className].filter(Boolean).join(" ")}>
      <p>{error.summary}</p>
      {error.technicalDetail && (
        <details>
          <summary>{t("View technical details")}</summary>
          <code aria-label={t("Technical error details")}>{error.technicalDetail}</code>
        </details>
      )}
    </div>
  );
}
