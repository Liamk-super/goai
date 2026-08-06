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
    <section className="report-insight-grid reveal"><article><small>当前阶段建议</small><h2>{report.recommendation.replaceAll("_"," ")}</h2><p>建议由规则与证据等级决定，不输出虚构的爆款概率。</p></article><article><small>关键矛盾 / 最大风险</small><h3>{(report.key_contradictions??report.blocking_reasons)[0]??"尚无硬阻塞"}</h3><p>{(report.key_contradictions??report.blocking_reasons).slice(1).join(" · ")||"继续观察反对证据与时效变化。"}</p></article><article><small>最大机会</small><h3>{Object.entries(report.dimension_results??{}).sort(([,a],[,b])=>b.grade.localeCompare(a.grade))[0]?.[0]?.replaceAll("_"," ")??"待建立"}</h3><p>优先验证证据最强且能改变投入决策的方向。</p></article><article><small>信息缺口</small><h3>{report.information_gaps?.length??0} 项待验证</h3><p>{report.information_gaps?.map(item=>item.replaceAll("_"," ")).join(" · ")||"当前四维均已形成可用证据。"}</p></article></section>
    <section className="panel reveal"><p className="panel-kicker">{t("Next validation cycle")}</p><h2>下一轮最值得执行的行动</h2><ol>{(report.action_links??report.action_items.map(action=>({action,dimension:null,evidence_ids:[]}))).slice(0,3).map(item=><li key={item.action}>{item.action}{item.dimension&&<small> · {item.dimension.replaceAll("_"," ")} · {item.evidence_ids.length} evidence</small>}</li>)}</ol>{(report.key_contradictions??report.blocking_reasons).length>0&&<p role="alert">{(report.key_contradictions??report.blocking_reasons).join(" · ")}</p>}</section>
    <section className="panel reveal"><div className="panel-header"><div><p className="panel-kicker">证据校准</p><h2>被降级或驳回的结论</h2></div><span>{report.calibration_results?.length??0} 项</span></div>{report.calibration_results?.length?<ul className="run-list">{report.calibration_results.map(item=><li key={item.finding_id}><div><strong>{item.decision}</strong><p>{item.reason}</p></div><code>{item.finding_id.slice(0,8)}</code></li>)}</ul>:<p>本轮没有需要展示的降级或驳回记录。</p>}</section>
    <section className="panel reveal"><div className="panel-header"><div><p className="panel-kicker">{t("Lineage")}</p><h2>{t("Finding → Evidence")}</h2></div><span>{t("{count} links", { count: report.evidence_chain.length })}</span></div><EvidenceChain items={report.evidence_chain}/></section>
    <section className="panel reveal"><p className="panel-kicker">版本复验</p><h2>修改产品后，按同一任务再检查一次。</h2><p className="lede">V2 会读取本轮未解决问题并重新获取政策、价格、平台规则和行业趋势，不直接复制旧结论。</p><a className="button" href={`/projects/${report.project_id}/new-evaluation`}>提交新版本并复验</a></section>
  </>}</main>;
}
