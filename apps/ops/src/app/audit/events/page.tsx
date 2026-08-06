"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { opsApi, type OpsEvent } from "../../../lib/ops-api";

export default function OpsEventsPage() {
  const [events, setEvents] = useState<OpsEvent[]>([]); const [error, setError] = useState<string>(); const [loading, setLoading] = useState(true);
  useEffect(() => { opsApi.listEvents().then(result => setEvents(result.items)).catch(cause => setError(cause instanceof Error ? cause.message : "Ops projection unavailable")).finally(() => setLoading(false)); }, []);
  return <main><section className="mast"><div><p className="eyebrow">Operational ledger</p><h1>Audit pulse.</h1><p className="lede">A deliberately narrow view of durable run transitions. No prompts, materials, findings, evidence bodies, or private reasoning cross this boundary.</p></div><div className="scope-stamp">metadata / append-only</div></section>{error && <p role="alert">{error}</p>}<section className="panel" aria-busy={loading}>{loading ? <div className="empty">Reading the redacted ledger…</div> : events.length === 0 ? <div className="empty">No operational events have been committed.</div> : <table><thead><tr><th>Event</th><th>Run</th><th>Delivery</th><th>Occurred</th></tr></thead><tbody>{events.map(event => <tr key={event.event_id}><td><span className="event-type">{event.event_type}</span></td><td><Link className="run-link" href={`/audit/runs/${event.run_id}`}><code>{event.run_id}</code></Link></td><td><span className="status">{event.status}</span></td><td>{new Date(event.occurred_at).toLocaleString()}</td></tr>)}</tbody></table>}</section></main>;
}
