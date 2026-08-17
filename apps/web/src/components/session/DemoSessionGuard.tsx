"use client";

import { useEffect, useState, type ReactNode } from "react";

import { restoreDemoSession } from "../../lib/demo-session-recovery";
import { useI18n } from "../i18n/LocaleProvider";

export function DemoSessionGuard({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const [state, setState] = useState<"checking" | "ready" | "error">("checking");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    restoreDemoSession(window.localStorage, controller.signal).then(() => {
      setState("ready");
    }).catch(error => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setMessage(error instanceof Error ? error.message : t("Demo session validation failed"));
      setState("error");
    });
    return () => controller.abort();
  }, [t]);

  if (state === "checking") return <main><div className="empty-state"><strong>{t("Validating local Demo identity…")}</strong></div></main>;
  if (state === "error") return <main><div className="empty-state" role="alert"><strong>{t("Demo session unavailable")}</strong><p>{message}</p></div></main>;
  return children;
}
