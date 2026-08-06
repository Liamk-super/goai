const snapshots = [
  {
    label: "V1 baseline", folder: "browser-20260806-005200",
    result: "LOCAL DETERMINISTIC ACCEPTANCE", hash: "d44cf3a9c889beb3cc70ecb70adbdd0049c5707a3e49a5854a52ef3591ec1f8d",
  },
  {
    label: "V2 remediation", folder: "interactive-20260806-012835",
    result: "LOCAL BROWSER ACCEPTANCE", hash: "Recorded bundle contains per-file SHA-256 indexes",
  },
] as const;

export default function RecordedSnapshotPage() {
  return <main>
    <header className="page-header reveal"><div><p className="eyebrow">Fallback / read only</p><h1>Recorded acceptance snapshot</h1><p className="lede">A non-interactive, sanitized fallback from the previously accepted local V1/V2 flow. It never dispatches a Run and is not evidence of live AgentTeams, Matrix, model, browser-provider, or search execution.</p></div><span className="status-pill danger"><i />BLOCKED NO AUTHORIZED CASE</span></header>
    <section className="panel reveal"><p className="panel-kicker">Recorded facts</p><h2>Local evidence, clearly bounded.</h2><div className="dimension-grid">{snapshots.map(snapshot=><article className="dimension" key={snapshot.label}><small>{snapshot.label}</small><strong>{snapshot.result}</strong><span>{snapshot.folder}</span><code>{snapshot.hash}</code></article>)}</div></section>
    <section className="panel reveal"><p className="panel-kicker">External acceptance</p><h2>Not substituted by this page.</h2><p role="alert">BLOCKED_NO_AUTHORIZED_CASE — provide an authorized URL plus model and search credentials before claiming real v0.2 E2E.</p></section>
  </main>;
}
