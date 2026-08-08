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
  return <main className="workspace-main">
    <header className="page-head enters">
      <span className="bearing">Fallback / read only</span>
      <div className="page-head-row">
        <h1>Recorded acceptance snapshot</h1>
        <span className="pill danger"><i />BLOCKED NO AUTHORIZED CASE</span>
      </div>
      <p>A non-interactive, sanitized fallback from the previously accepted local V1/V2 flow. It never dispatches a Run and is not evidence of live AgentTeams, Matrix, model, browser-provider, or search execution.</p>
    </header>
    <section className="plate enters">
      <p className="plate-kicker">Recorded facts</p>
      <h2>Local evidence, clearly bounded.</h2>
      <div className="grid-auto">{snapshots.map(snapshot=>
        <dl className="readout" key={snapshot.label}>
          <dt>{snapshot.label}</dt>
          <dd>{snapshot.result}<span className="bearing" style={{ marginTop: 6 }}>{snapshot.folder}</span><span className="bearing">{snapshot.hash}</span></dd>
        </dl>)}
      </div>
    </section>
    <section className="plate plate-quiet enters">
      <p className="plate-kicker">External acceptance</p>
      <h2>Not substituted by this page.</h2>
      <p role="alert">BLOCKED_NO_AUTHORIZED_CASE — provide an authorized URL plus model and search credentials before claiming real v0.2 E2E.</p>
    </section>
  </main>;
}
