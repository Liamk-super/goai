"use client";

import { use, useEffect, useState } from "react";
import { browserApi } from "../../../../../../lib/api-client";
import { PageHeader, StatusPill } from "../../../../../../components/shell/AppShell";
import { useI18n } from "../../../../../../components/i18n/LocaleProvider";

type Comparison={project_id:string;baseline_run_id:string;candidate_run_id:string;comparable:boolean;standard_version:string;supplemental_standard_version?:string|null;baseline_status:string;candidate_status:string;dimension_changes:Record<string,string>;new_risks:string[]};
export default function ComparePage({ params }: { params: Promise<{ projectId: string; runId: string }> }) {
  const { t } = useI18n();
  const { projectId, runId } = use(params); const [comparison,setComparison]=useState<Comparison>(); const [error,setError]=useState<string>();
  useEffect(()=>{void browserApi().compare(projectId,runId).then(value=>setComparison(value as unknown as Comparison)).catch(cause=>setError(cause.message));},[projectId,runId]);
  return <main><PageHeader eyebrow={t("Version regression")} title={t("Same standard. New truth.")} description={t("A comparison is valid only when project, core tasks and standard remain frozen. Changed standards appear as supplemental—not a silent rewrite.")} action={comparison&&<StatusPill value={comparison.comparable?"COMPARABLE":"STANDARD DRIFT"}/>} />{error&&<p role="alert">{error}</p>}{comparison&&<>
    <section className="metric-row reveal"><div className="metric"><small>{t("Standard")}</small><strong>{comparison.standard_version}</strong></div><div className="metric"><small>{t("Baseline")}</small><strong>{comparison.baseline_status}</strong></div><div className="metric"><small>{t("Candidate")}</small><strong>{comparison.candidate_status}</strong></div><div className="metric"><small>{t("Result")}</small><strong>{comparison.comparable?t("Valid"):t("Split")}</strong></div></section>
    <section className="signal-grid reveal"><article className="project-card"><span className="number">{t("BASELINE / V1")}</span><h2>{comparison.baseline_run_id.slice(0,8)}</h2><p><code>{comparison.baseline_run_id}</code></p><StatusPill value={comparison.baseline_status}/></article><article className="project-card"><span className="number">{t("CANDIDATE / V2")}</span><h2>{comparison.candidate_run_id.slice(0,8)}</h2><p><code>{comparison.candidate_run_id}</code></p><StatusPill value={comparison.candidate_status}/></article></section>
    {Object.keys(comparison.dimension_changes).length>0&&<section className="dimension-grid reveal">{Object.entries(comparison.dimension_changes).map(([dimension,change])=><article className="dimension" key={dimension}><small>{dimension.replaceAll("_"," ")}</small><strong>{change}</strong></article>)}</section>}
    <section className="report-insight-grid reveal"><article><small>已解决问题</small><h3>{Object.values(comparison.dimension_changes).filter(value=>value==="IMPROVED").length} 项</h3><p>{Object.entries(comparison.dimension_changes).filter(([,value])=>value==="IMPROVED").map(([key])=>key.replaceAll("_"," ")).join(" · ")||"没有维度获得更高证据等级。"}</p></article><article><small>仍未解决</small><h3>{Object.values(comparison.dimension_changes).filter(value=>value!=="IMPROVED").length} 项</h3><p>{Object.entries(comparison.dimension_changes).filter(([,value])=>value!=="IMPROVED").map(([key,value])=>`${key.replaceAll("_"," ")} ${value}`).join(" · ")}</p></article><article><small>表面完成但复验失败</small><h3>{Object.values(comparison.dimension_changes).filter(value=>value==="REGRESSED").length} 项</h3><p>只有重新执行后证据等级下降的维度才列入，不用修改说明代替测试。</p></article><article><small>证据等级变化</small><h3>{comparison.comparable?"同标准可比":"标准已漂移"}</h3><p>{comparison.comparable?"核心任务与标准版本保持一致。":"新标准结果必须作为补充结果单独展示。"}</p></article></section>
    {comparison.new_risks.length>0&&<section className="panel reveal"><p className="panel-kicker">{t("New risks")}</p><p role="alert">{comparison.new_risks.join(" · ")}</p></section>}
  </>}</main>;
}
