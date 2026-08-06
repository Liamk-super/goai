import "../(workspace)/globals.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "LaunchScope recorded acceptance snapshot",
  description: "Read-only sanitized fallback; not live execution evidence",
};

export default function SnapshotLayout({ children }: { children: ReactNode }) {
  return <html lang="en"><body><div className="app-frame"><div className="demo-identity-banner"><strong>Recorded acceptance snapshot</strong><span>Read only · not live AgentTeams execution</span><a href="/demo-login">Enter live Demo</a></div>{children}<footer><span>Evidence before assertion.</span><span>No writes are available on this route.</span></footer></div></body></html>;
}
