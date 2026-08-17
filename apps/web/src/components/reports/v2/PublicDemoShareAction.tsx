"use client";

import { useState } from "react";
import { browserApi } from "../../../lib/api-client";
import { useI18n } from "../../i18n/LocaleProvider";

export function PublicDemoShareAction({ reportId }: { reportId: string }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [href, setHref] = useState<string>();
  const [error, setError] = useState<string>();

  async function publish() {
    setBusy(true);
    setError(undefined);
    try {
      const share = await browserApi().createPublicDemoShare(reportId);
      setHref(`${window.location.origin}/shared/demo/${encodeURIComponent(share.token)}/reports/${encodeURIComponent(reportId)}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Public link creation failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="plate plate-quiet report-v22-public-share" aria-label={t("Public Demo link")}>
      <div>
        <span className="plate-kicker">{t("Public Demo link")}</span>
        <p>{t("The read-only link includes the supervisor report, four specialist reports, sources, and evidence.")}</p>
      </div>
      {!href && <button type="button" className="button secondary" disabled={busy} onClick={() => void publish()}>{busy ? t("Publishing…") : t("Publish public demo link")}</button>}
      {href && <a className="button" href={href}>{t("Open public report")}</a>}
      {error && <p className="error-banner" role="alert">{error}</p>}
    </section>
  );
}
