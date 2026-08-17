"use client";

import { useI18n } from "../i18n/LocaleProvider";

type PublicDemoDisclosureProps = {
  open: boolean;
  busy: boolean;
  error?: string;
  onAccept: () => void;
};

export function PublicDemoDisclosure({ open, busy, error, onAccept }: PublicDemoDisclosureProps) {
  const { t } = useI18n();
  if (!open) return null;
  return (
    <div className="history-dialog-backdrop" role="presentation">
      <section
        className="history-dialog public-demo-disclosure"
        role="dialog"
        aria-modal="true"
        aria-labelledby="public-demo-disclosure-title"
      >
        <span className="bearing">{t("Public disclosure")}</span>
        <h2 id="public-demo-disclosure-title">
          {t("Public Demo: uploaded materials may be displayed publicly in the report Evidence chain.")}
        </h2>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button type="button" onClick={onAccept} disabled={busy} autoFocus>
          {busy ? t("Recording…") : t("I understand, continue uploading")}
        </button>
      </section>
    </div>
  );
}
