"use client";

import { use, useEffect, useState } from "react";
import { opsApi, type OpsRun } from "../../../../lib/ops-api";

export default function OpsRunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = use(params); const [run, setRun] = useState<OpsRun>(); const [error, setError] = useState<string>();
  useEffect(() => { opsApi.getRun(runId).then(setRun).catch(cause => setError(cause instanceof Error ? cause.message : "Ops projection unavailable")); }, [runId]);
  return <main><section className="mast"><div><p className="eyebrow">Run projection</p><h1>Boundary view.</h1><p className="lede">Operational state required for triage, exposed through a separate Ops identity and database role.</p></div><div className="scope-stamp">run / {runId.slice(0, 8)}</div></section><div className="redaction-note">Content boundary enforced: tenant report, material, evidence, prompt, finding and private reasoning fields are absent from this response.</div>{error && <p role="alert">{error}</p>}{run && <section className="panel"><dl className="record">{Object.entries(run).map(([key, value]) => <div key={key} style={{display:"contents"}}><dt>{key.replaceAll("_", " ")}</dt><dd>{String(value ?? "—")}</dd></div>)}</dl></section>}</main>;
}
