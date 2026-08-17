"use client";

import { useI18n } from "../../../components/i18n/LocaleProvider";

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
  const { t } = useI18n();
  return <main className="workspace-main">
    <header className="page-head enters">
      <span className="bearing">{t("Fallback / read only")}</span>
      <div className="page-head-row">
        <h1>{t("Recorded acceptance snapshot")}</h1>
        <span className="pill danger"><i />{t("BLOCKED NO AUTHORIZED CASE")}</span>
      </div>
      <p>{t("A non-interactive, sanitized fallback from the previously accepted local V1/V2 flow. It never dispatches a Run and is not evidence of live AgentTeams, Matrix, model, browser-provider, or search execution.")}</p>
    </header>
    <section className="plate enters">
      <p className="plate-kicker">{t("Recorded facts")}</p>
      <h2>{t("Local evidence, clearly bounded.")}</h2>
      <div className="grid-auto">{snapshots.map(snapshot=>
        <dl className="readout" key={snapshot.label}>
          <dt>{t(snapshot.label)}</dt>
          <dd>{t(snapshot.result)}<span className="bearing" style={{ marginTop: 6 }}>{snapshot.folder}</span><span className="bearing">{t(snapshot.hash)}</span></dd>
        </dl>)}
      </div>
    </section>
    <section className="plate plate-quiet enters">
      <p className="plate-kicker">{t("External acceptance")}</p>
      <h2>{t("Not substituted by this page.")}</h2>
      <p role="alert">{t("BLOCKED_NO_AUTHORIZED_CASE — provide an authorized URL plus model and search credentials before claiming real v0.2 E2E.")}</p>
    </section>
  </main>;
}
