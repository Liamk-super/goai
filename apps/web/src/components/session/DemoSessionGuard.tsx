"use client";

import { useEffect, useState, type ReactNode } from "react";

import { apiBase, ApiError } from "../../lib/api-client";
import { clearDemoSession, loadDemoSession } from "../../lib/demo-session";

export function DemoSessionGuard({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"checking" | "ready" | "error">("checking");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const session = loadDemoSession(window.localStorage);
    if (!session) {
      window.location.replace("/demo-login");
      return;
    }
    const controller = new AbortController();
    fetch(`${apiBase()}/api/v1/demo/session`, {
      credentials: "include",
      signal: controller.signal,
      headers: {
        "X-Tenant-Id": session.tenantId,
        "X-Actor-Id": session.actorId,
        "X-Workspace-Id": session.workspaceId,
        "X-Correlation-Id": crypto.randomUUID(),
      },
    }).then(async response => {
      if (response.ok) {
        setState("ready");
        return;
      }
      if ([403, 404].includes(response.status)) {
        clearDemoSession(window.localStorage);
        window.location.replace("/demo-login");
        return;
      }
      throw new ApiError(response.status, await response.json().catch(() => ({})));
    }).catch(error => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setMessage(error instanceof Error ? error.message : "Demo session validation failed");
      setState("error");
    });
    return () => controller.abort();
  }, []);

  if (state === "checking") return <main><div className="empty-state"><strong>Validating local Demo identity…</strong></div></main>;
  if (state === "error") return <main><div className="empty-state" role="alert"><strong>Demo session unavailable</strong><p>{message}</p></div></main>;
  return children;
}
