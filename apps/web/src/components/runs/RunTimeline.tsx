"use client";

import type { SseEvent } from "../../lib/sse-client";
import { useI18n } from "../i18n/LocaleProvider";

export function RunTimeline({ events }: { events: SseEvent[] }) {
  const { t } = useI18n();
  if (!events.length) return <div className="empty-state"><strong>{t("Listening for durable state.")}</strong><p>{t("SSE will resume from the last PostgreSQL cursor after a reconnect.")}</p></div>;
  return <ol className="timeline" aria-label={t("Run timeline")}>{events.map(event => <li key={event.id}><time>{event.id}</time><h3>{event.event.replaceAll(".", " / ")}</h3><pre>{JSON.stringify(event.data, null, 2)}</pre></li>)}</ol>;
}
