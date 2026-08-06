import Link from "next/link";
import "./globals.css";

export const metadata = {
  title: "LaunchScope Ops",
  description: "Redacted operational audit surface",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const actorId = process.env.NEXT_PUBLIC_LAUNCHSCOPE_OPS_ACTOR_ID ?? "";
  return (
    <html lang="en">
      <head><meta name="launchscope-ops-actor-id" content={actorId} /></head>
      <body>
        <div className="ops-frame">
          <header className="ops-bar">
            <Link href="/audit/events" className="ops-brand"><span>LS</span><strong>LaunchScope / Ops</strong></Link>
            <div className="boundary"><i /> redacted projection only</div>
          </header>
          {children}
          <footer><span>Separate identity domain</span><span>Tenant content prohibited</span></footer>
        </div>
      </body>
    </html>
  );
}
