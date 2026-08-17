"use client";

import type { ReactNode } from "react";
import { LocaleSelect, useI18n } from "../i18n/LocaleProvider";

/** 罗经玫瑰标记 —— 航海仪器的品牌符号，替代 "LS" 字母块 */
function CompassRose() {
  return (
    <svg className="brand-mark" viewBox="0 0 40 40" aria-hidden="true">
      <circle cx="20" cy="20" r="18.5" fill="none" stroke="currentColor" strokeWidth="1" opacity=".45" />
      <circle cx="20" cy="20" r="14" fill="none" stroke="currentColor" strokeWidth=".6" opacity=".25" />
      {Array.from({ length: 8 }, (_, i) => {
        const a = (i * 45 * Math.PI) / 180;
        const inner = i % 2 === 0 ? 4 : 9;
        return (
          <line
            key={i}
            x1={20 + inner * Math.sin(a)}
            y1={20 - inner * Math.cos(a)}
            x2={20 + 17 * Math.sin(a)}
            y2={20 - 17 * Math.cos(a)}
            stroke="currentColor"
            strokeWidth={i % 2 === 0 ? 1 : 0.5}
            opacity={i % 2 === 0 ? 0.7 : 0.3}
          />
        );
      })}
      {/* 朱红指北针 —— 全站唯一强调色 */}
      <path d="M20 4 L23 19 L20 24 L17 19 Z" fill="#c2410c" />
    </svg>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { t } = useI18n();

  return (
    <div className="app-frame">
      <header className="topbar">
        <a className="brand" href="/" aria-label={t("LaunchScope")}>
          <CompassRose />
          <span>
            <span className="brand-name">{t("LaunchScope")}</span>
            <span className="brand-sub">{t("LaunchScope · evidence instrument")}</span>
          </span>
        </a>
        <nav className="topnav" aria-label={t("Primary navigation")}>
          <a href="/projects">{t("Projects")}</a>
          <a href="/?start=1">{t("New signal")}</a>
        </nav>
        <div className="topbar-right">
          <LocaleSelect compact />
        </div>
      </header>

      {children}

      <footer className="app-footer">
        <span>{t("Make every prediction evidence-based.")}</span>
      </footer>
    </div>
  );
}

export function SnapshotShell({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="app-frame">
      <div className="demo-banner">
        <span>{t("Recorded acceptance snapshot · Read only · not live AgentTeams execution")}</span>
        <a href="/">{t("Enter live Demo")}</a>
      </div>
      {children}
      <footer className="app-footer">
        <span>{t("Evidence before assertion.")}</span>
        <span>{t("No writes are available on this route.")}</span>
      </footer>
    </div>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-head enters">
      <div className="page-head-row">
        <div>
          <span className="bearing">{eyebrow}</span>
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
        {action && <div>{action}</div>}
      </div>
    </header>
  );
}

export function StatusPill({ value }: { value: string }) {
  const { status } = useI18n();
  return (
    <span className="status" data-state={value.toLowerCase()}>
      {status(value)}
    </span>
  );
}
