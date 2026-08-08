"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useI18n } from "../i18n/LocaleProvider";
import { clearDemoSession, loadDemoSession } from "../../lib/demo-session";

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
  const { locale, setLocale, t } = useI18n();
  const [displayName, setDisplayName] = useState("Demo user");
  useEffect(() => {
    setDisplayName(loadDemoSession(window.localStorage)?.displayName ?? "Demo user");
  }, []);

  return (
    <div className="app-frame">
      <div className="demo-banner">
        <span>本地体验身份 · {displayName} · 非生产登录</span>
        <button
          className="quiet"
          style={{ color: "inherit", textDecoration: "underline" }}
          onClick={() => {
            clearDemoSession(window.localStorage);
            window.location.assign("/demo-login");
          }}
        >
          退出体验
        </button>
      </div>

      <header className="topbar">
        <a className="brand" href="/projects" aria-label={t("LaunchScope projects")}>
          <CompassRose />
          <span>
            <span className="brand-name">势能引擎</span>
            <span className="brand-sub">LaunchScope · evidence instrument</span>
          </span>
        </a>
        <nav className="topnav" aria-label={t("Primary navigation")}>
          <a href="/projects">{t("Projects")}</a>
          <a href="/projects/new">{t("New signal")}</a>
          <a href="/recorded-snapshot">{t("Recorded snapshot")}</a>
        </nav>
        <div className="topbar-right">
          <label style={{ display: "flex", alignItems: "center", gap: 8, margin: 0 }}>
            <span className="bearing">{t("Language")}</span>
            <select
              aria-label={t("Language")}
              value={locale}
              onChange={(event) => setLocale(event.target.value as "en" | "zh-CN")}
            >
              <option value="en">English</option>
              <option value="zh-CN">简体中文</option>
            </select>
          </label>
          <span className="status" data-state="completed">
            {t("PostgreSQL truth")}
          </span>
        </div>
      </header>

      {children}

      <footer className="app-footer">
        <span>{t("Evidence before assertion.")}</span>
        <span>22.3193° N · 114.1694° E / V0.3</span>
        <span>{t("Read-only by default · fail-closed always.")}</span>
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
