import "../(workspace)/globals.css";
import type { ReactNode } from "react";
import { LocaleProvider } from "../../components/i18n/LocaleProvider";
import { SnapshotShell } from "../../components/shell/AppShell";
import { localizedMetadata, requestLocale } from "../../lib/locale-server";

export async function generateMetadata() {
  const locale = await requestLocale();
  return localizedMetadata(locale, {
    en: { title: "Hit Predictor · Recorded Acceptance Snapshot", description: "Sanitized read-only snapshot; not evidence of live execution" },
    "zh-CN": { title: "爆款预测器 · 只读验收快照", description: "脱敏只读快照，不代表实时执行证据" },
  });
}

export default async function SnapshotLayout({ children }: { children: ReactNode }) {
  const locale = await requestLocale();
  return <html lang={locale}><body>
    <LocaleProvider initialLocale={locale}><SnapshotShell>{children}</SnapshotShell></LocaleProvider>
  </body></html>;
}
