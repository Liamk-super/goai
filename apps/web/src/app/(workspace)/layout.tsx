import { localizedMetadata, requestLocale } from "../../lib/locale-server";

export async function generateMetadata() {
  const locale = await requestLocale();
  return localizedMetadata(locale, {
    en: { title: "Hit Predictor · Workspace", description: "Evidence-based product hit prediction" },
    "zh-CN": { title: "爆款预测器 · 工作台", description: "用真实证据预测产品的爆款潜力" },
  });
}

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const locale = await requestLocale();
  return (
    <html lang={locale}>
      <body><LocaleProvider initialLocale={locale}><DemoSessionGuard><AppShell>{children}</AppShell></DemoSessionGuard></LocaleProvider></body>
    </html>
  )
}
import "./globals.css";
import { AppShell } from "../../components/shell/AppShell";
import { LocaleProvider } from "../../components/i18n/LocaleProvider";
import { DemoSessionGuard } from "../../components/session/DemoSessionGuard";
