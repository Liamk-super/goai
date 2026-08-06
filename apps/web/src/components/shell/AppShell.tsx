"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useI18n } from "../i18n/LocaleProvider";
import { clearDemoSession, loadDemoSession } from "../../lib/demo-session";

export function AppShell({ children }: { children: ReactNode }) {
  const { locale, setLocale, t } = useI18n();
  const [displayName, setDisplayName] = useState("Demo user");
  useEffect(() => {
    setDisplayName(loadDemoSession(window.localStorage)?.displayName ?? "Demo user");
  }, []);
  return <div className="app-frame">
    <div className="demo-identity-banner"><strong>Local Demo Identity</strong><span>{displayName} · not production authentication</span><button className="text-button" onClick={() => { clearDemoSession(window.localStorage); window.location.assign("/demo-login"); }}>Exit Demo</button></div>
    <header className="topbar">
      <a className="brand" href="/projects" aria-label={t("LaunchScope projects")}>
        <span className="brand-mark" aria-hidden="true">LS</span>
        <span><strong>LaunchScope</strong><small>{t("evidence command")}</small></span>
      </a>
      <nav aria-label={t("Primary navigation")}>
        <a href="/projects">{t("Projects")}</a>
        <a href="/projects/new">{t("New signal")}</a>
        <a href="/recorded-snapshot">{t("Recorded snapshot")}</a>
      </nav>
      <div className="topbar-actions">
        <label className="locale-picker"><span>{t("Language")}</span><select aria-label={t("Language")} value={locale} onChange={event => setLocale(event.target.value as "en" | "zh-CN")}><option value="en">English</option><option value="zh-CN">简体中文</option></select></label>
        <div className="live-indicator"><i /> {t("PostgreSQL truth")}</div>
      </div>
    </header>
    <div className="coordinate" aria-hidden="true">22.3193° N · 114.1694° E / V0.2</div>
    {children}
    <footer><span>{t("Evidence before assertion.")}</span><span>{t("Read-only by default · fail-closed always.")}</span></footer>
  </div>;
}

export function PageHeader({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return <header className="page-header reveal">
    <div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p className="lede">{description}</p></div>
    {action && <div className="header-action">{action}</div>}
  </header>;
}

export function StatusPill({ value }: { value: string }) {
  const { status } = useI18n();
  const tone = /COMPLETED|VALIDATED|PROCEED/.test(value) ? "positive" : /ATTENTION|FAILED|BLOCKED|PAUSE/.test(value) ? "danger" : "neutral";
  return <span className={`status-pill ${tone}`}><i />{status(value)}</span>;
}
