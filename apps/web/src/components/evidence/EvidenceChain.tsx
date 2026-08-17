"use client";

import { useState } from "react";
import { browserApi, type EvidenceNode } from "../../lib/api-client";
import { useI18n } from "../i18n/LocaleProvider";
import { LocalizedErrorMessage } from "../i18n/LocalizedErrorMessage";

export function EvidenceChain({ items, readOnly = false }: { items: EvidenceNode[]; readOnly?: boolean }) {
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
  return <>{error && <LocalizedErrorMessage value={error} />}<ol className="evidence-chain" aria-label={t("Evidence chain")}>{items.map(item => <li key={`${item.finding_id}:${item.evidence_id}`}><details><summary>{t("View supporting evidence")}</summary><dl><dt>{t("Source")}</dt><dd>{item.source_type} / {item.trust_level}</dd></dl>{!readOnly && <button type="button" className="secondary" onClick={() => void openEvidence(item.evidence_id)}>{t("Open evidence")}</button>}</details></li>)}</ol></>;
}
