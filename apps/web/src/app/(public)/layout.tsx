import "../(workspace)/globals.css";
import { LocaleProvider } from "../../components/i18n/LocaleProvider";
import { localizedMetadata, requestLocale } from "../../lib/locale-server";

export async function generateMetadata() {
  const locale = await requestLocale();
  return {
    ...localizedMetadata(locale, {
      en: { title: "Hit Predictor", description: "Predict product potential with evidence" },
      "zh-CN": { title: "爆款预测器", description: "用真实证据预测产品的爆款潜力" },
    }),
    robots: { index: false, follow: false },
  };
}

export default async function PublicLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const locale = await requestLocale();
  return (
    <html lang={locale}>
      <body><LocaleProvider initialLocale={locale}>{children}</LocaleProvider></body>
    </html>
  );
}
