"use client";

import { use } from "react";
import { useI18n } from "../../../../../../components/i18n/LocaleProvider";

export default function VersionPage({ params }: { params: Promise<{ projectId: string; versionId: string }> }) {
  const { t } = useI18n();
  const { projectId, versionId } = use(params);
  return <main><h1>{t("Product version")}</h1><p>{t("Version {version} belongs to this project’s immutable dossier history.", { version: versionId })}</p>
    <a href={`/projects/${projectId}/new-evaluation?versionId=${encodeURIComponent(versionId)}`}>{t("Continue evaluation intake")}</a>
  </main>;
}
