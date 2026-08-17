import type { ReactNode } from "react";

import "../(workspace)/globals.css";
import { LocaleProvider } from "../../components/i18n/LocaleProvider";
import { localizedMetadata, requestLocale } from "../../lib/locale-server";

export async function generateMetadata() {
  const locale = await requestLocale();
  return localizedMetadata(locale, {
    en: { title: "Hit Predictor · Local Demo Sign-in", description: "Local demo entry for the Hit Predictor" },
    "zh-CN": { title: "爆款预测器 · 本地体验登录", description: "爆款预测器本地体验身份入口" },
  });
}

export default async function DemoLoginLayout({ children }: { children: ReactNode }) {
  const locale = await requestLocale();
  return <html lang={locale}><body><LocaleProvider initialLocale={locale}>{children}</LocaleProvider></body></html>;
}
