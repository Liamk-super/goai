"use client";

import { use, useEffect, useState } from "react";
import { browserApi, type Report } from "../../../../lib/api-client";
import { EvidenceChain } from "../../../../components/evidence/EvidenceChain";
import { PageHeader, StatusPill } from "../../../../components/shell/AppShell";
import { useI18n } from "../../../../components/i18n/LocaleProvider";

export default function ReportPage({ params }: { params: Promise<{ reportId: string }> }) {
  const { t } = useI18n();
  const { reportId } = use(params); const [report, setReport] = useState<Report>(); const [error, setError] = useState<string>();
  useEffect(() => { void browserApi().getReport(reportId).then(setReport).catch(cause => setError(cause.message)); }, [reportId]);
  return <main><PageHeader eyebrow={t("Decision / {id}", { id: reportId.slice(0,8) })} title={t("A verdict with receipts.")} description={t("Rules determine the grade and hard blocks. Explanation makes it readable; Evidence makes it accountable.")} action={report && <StatusPill value={report.recommendation} />} />{error && <p role="alert">{error}</p>}{report && <>
    <section className="dimension-grid reveal">{Object.entries(report.dimension_results??Object.fromEntries(Object.entries(report.dimension_grades).map(([key,grade])=>[key,{grade,evidence_confidence:"—",change:"NO_BASELINE"}]))).map(([dimension,result],index)=><article className="dimension" key={dimension}><small>0{index+1} / {dimension.replaceAll("_"," ")}</small><strong>{result.grade.replaceAll("_"," ")}</strong><span>{t("Evidence {level} · {change}",{level:result.evidence_confidence,change:result.change.replaceAll("_"," ")})}</span></article>)}</section>
    {report.geo_trend&&<section className="panel reveal"><p className="panel-kicker">{t("Time / region trend")}</p><h2>{report.geo_trend.signal}</h2><p>{t("Region {region} · as of {date}",{region:report.geo_trend.region??t("Not established"),date:report.geo_trend.as_of??t("Not established")})}</p></section>}
    <section className="panel reveal"><p className="panel-kicker">{t("Next validation cycle")}</p><h2>{t("Actions, not theatre.")}</h2><ol>{(report.action_links??report.action_items.map(action=>({action,dimension:null,evidence_ids:[]}))).map(item=><li key={item.action}>{item.action}{item.dimension&&<small> · {item.dimension.replaceAll("_"," ")} · {item.evidence_ids.length} evidence</small>}</li>)}</ol>{(report.key_contradictions??report.blocking_reasons).length>0&&<p role="alert">{(report.key_contradictions??report.blocking_reasons).join(" · ")}</p>}</section>
    <section className="panel reveal"><div className="panel-header"><div><p className="panel-kicker">{t("Lineage")}</p><h2>{t("Finding → Evidence")}</h2></div><span>{t("{count} links", { count: report.evidence_chain.length })}</span></div><EvidenceChain items={report.evidence_chain}/></section>
  </>}</main>;
}
