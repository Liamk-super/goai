"use client";

import { useState } from "react";
import { browserApi, sessionFromDocument } from "../../../../lib/api-client";
import { PageHeader } from "../../../../components/shell/AppShell";
import { useI18n } from "../../../../components/i18n/LocaleProvider";

export default function NewProjectPage() {
  const { t } = useI18n();
  const [name, setName] = useState(""); const [error, setError] = useState<string>(); const [busy, setBusy] = useState(false);
  async function create(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(undefined);
    try {
      const workspaceId = sessionFromDocument().workspaceId;
      if (!workspaceId) throw new Error(t("No workspace is attached to this session. Use the local bootstrap output or sign in again."));
      const project = await browserApi().createProject(workspaceId, name);
      window.location.assign(`/projects/${project.project_id}`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : t("Project creation failed")); setBusy(false); }
  }
  return <main><PageHeader eyebrow={t("Signal intake / 00")} title={t("Name the question.")} description={t("A Project persists across V1, V2 and every later challenge to the thesis. Keep the name stable; let evidence change the answer.")} />
    <section className="panel reveal"><form onSubmit={create}><label>{t("Project name")}<input autoFocus required minLength={2} maxLength={200} value={name} onChange={event => setName(event.target.value)} placeholder={t("e.g. Merchant onboarding engine")} /></label>{error && <p role="alert">{error}</p>}<div className="form-actions"><button disabled={busy}>{busy ? t("Committing…") : t("Create durable dossier")}</button><a className="button secondary" href="/projects">{t("Cancel")}</a></div></form></section>
  </main>;
}
