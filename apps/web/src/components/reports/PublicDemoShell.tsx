"use client";

import type { ReactNode } from "react";
import { LocaleSelect, useI18n } from "../i18n/LocaleProvider";

export function PublicDemoShell({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="app-frame">
      <header className="topbar">
        <span className="brand" aria-label={t("LaunchScope public read-only demo")}>
          <span>
            <span className="brand-name">{t("LaunchScope")}</span>
            <span className="brand-sub">{t("Public read-only demo · no sign-in required")}</span>
          </span>
        </span>
        <div className="topbar-right">
          <LocaleSelect compact />
        </div>
      </header>
      {children}
      <footer className="app-footer">
        <span>{t("Read-only share: no writes or project listing; shared evidence remains token-scoped.")}</span>
      </footer>
    </div>
  );
}
