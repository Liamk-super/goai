export const metadata = {
  title: "LaunchScope workspace",
  description: "Durable PostgreSQL-backed product validation workspace",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body><LocaleProvider><DemoSessionGuard><AppShell>{children}</AppShell></DemoSessionGuard></LocaleProvider></body>
    </html>
  )
}
import "./globals.css";
import { AppShell } from "../../components/shell/AppShell";
import { LocaleProvider } from "../../components/i18n/LocaleProvider";
import { DemoSessionGuard } from "../../components/session/DemoSessionGuard";
