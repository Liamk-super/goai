"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { normalizeLocale, translate, translateStatus, type Locale } from "../../lib/i18n";

const STORAGE_KEY = "launchscope.locale";
type I18nContextValue = {
  locale: Locale;
  setLocale(locale: Locale): void;
  t(key: string, values?: Record<string, string | number>): string;
  status(value: string): string;
};

const I18nContext = createContext<I18nContextValue | null>(null);

export function LocaleProvider({ children, initialLocale = "en" }: { children: ReactNode; initialLocale?: Locale }) {
  const router = useRouter();
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  useEffect(() => {
    const cookieLocale = document.cookie
      .split("; ")
      .find(value => value.startsWith(`${STORAGE_KEY}=`))
      ?.split("=")
      .slice(1)
      .join("=");
    if (cookieLocale) return;
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (!stored) return;
    const migrated = normalizeLocale(stored);
    document.cookie = `${STORAGE_KEY}=${encodeURIComponent(migrated)}; Path=/; Max-Age=31536000; SameSite=Lax`;
    setLocaleState(migrated);
  }, []);

  function setLocale(next: Locale) {
    window.localStorage.setItem(STORAGE_KEY, next);
    document.cookie = `${STORAGE_KEY}=${encodeURIComponent(next)}; Path=/; Max-Age=31536000; SameSite=Lax`;
    setLocaleState(next);
    router.refresh();
  }

  useEffect(() => { document.documentElement.lang = locale; }, [locale]);

  const value = useMemo<I18nContextValue>(() => ({
    locale,
    setLocale,
    t: (key, values) => translate(locale, key, values),
    status: value => translateStatus(locale, value),
  }), [locale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside LocaleProvider");
  return value;
}

export function LocaleSelect({ compact = false }: { compact?: boolean }) {
  const { locale, setLocale, t } = useI18n();
  return (
    <label className={compact ? "locale-select locale-select-compact" : "locale-select"}>
      {!compact && <span className="bearing">{t("Language")}</span>}
      <select
        aria-label={t("Language")}
        value={locale}
        onChange={(event) => setLocale(event.target.value as Locale)}
      >
        <option value="en">English</option>
        <option value="zh-CN">简体中文</option>
      </select>
    </label>
  );
}
