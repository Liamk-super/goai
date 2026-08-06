"use client";

import { useState } from "react";
import { browserApi, type EvidenceNode } from "../../lib/api-client";
import { useI18n } from "../i18n/LocaleProvider";

export function EvidenceChain({ items }: { items: EvidenceNode[] }) {
  const { t } = useI18n();
  const [error, setError] = useState<string>();
  async function openEvidence(evidenceId: string) {
    setError(undefined);
    try {
      const { read_url: url } = await browserApi().evidenceReadUrl(evidenceId);
      window.open(url, "_blank", "noopener,noreferrer");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Evidence cannot be opened."));
    }
  }
  if (items.length === 0) return <div className="empty-state"><strong>{t("No committed evidence.")}</strong><p>{t("Claims without evidence remain hypotheses and cannot drive the report.")}</p></div>;
  return <>{error && <p role="alert">{error}</p>}<ol className="evidence-chain" aria-label={t("Evidence chain")}>{items.map(item => <li key={`${item.finding_id}:${item.evidence_id}`}><details><summary>{t("Finding → immutable Evidence")}</summary><dl><dt>{t("Finding")}</dt><dd><code>{item.finding_id}</code></dd><dt>{t("Evidence")}</dt><dd><code>{item.evidence_id}</code></dd><dt>{t("Source")}</dt><dd>{item.source_type} / {item.trust_level}</dd><dt>{t("Private object")}</dt><dd><code>{item.object_key}</code></dd><dt>SHA-256</dt><dd><code>{item.sha256}</code></dd></dl><button type="button" className="secondary" onClick={() => void openEvidence(item.evidence_id)}>{t("Open signed evidence")}</button></details></li>)}</ol></>;
}
