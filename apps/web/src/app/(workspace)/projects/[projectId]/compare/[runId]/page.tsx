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
  return <main className="workspace-main">
    <PageHeader eyebrow={t("Version regression")} title={t("Same standard. New truth.")} description={t("A comparison is valid only when project, core tasks and standard remain frozen. Changed standards appear as supplemental—not a silent rewrite.")} action={comparison&&<StatusPill value={comparison.comparable?"COMPARABLE":"STANDARD DRIFT"}/>} />
    {error&&<p role="alert">{error}</p>}
    {comparison&&<>
      <div className="grid-auto enters">
        <dl className="readout"><dt>{t("Standard")}</dt><dd>{comparison.standard_version}</dd></dl>
        <dl className="readout"><dt>{t("Baseline")}</dt><dd>{comparison.baseline_status}</dd></dl>
        <dl className="readout"><dt>{t("Candidate")}</dt><dd>{comparison.candidate_status}</dd></dl>
        <dl className="readout"><dt>{t("Result")}</dt><dd>{comparison.comparable?t("Valid"):t("Split")}</dd></dl>
      </div>

      <section className="plate enters">
        <p className="plate-kicker">两个版本</p>
        <div className="grid-auto">
          <div>
            <span className="bearing">{t("BASELINE VERSION")}</span>
            <h3>{comparison.baseline_run_id.slice(0,8)}</h3>
            <dl className="readout"><dt>run id</dt><dd>{comparison.baseline_run_id}</dd></dl>
            <StatusPill value={comparison.baseline_status}/>
          </div>
          <div>
            <span className="bearing">{t("CANDIDATE VERSION")}</span>
            <h3>{comparison.candidate_run_id.slice(0,8)}</h3>
            <dl className="readout"><dt>run id</dt><dd>{comparison.candidate_run_id}</dd></dl>
            <StatusPill value={comparison.candidate_status}/>
          </div>
        </div>
      </section>

      {Object.keys(comparison.dimension_changes).length>0&&<section className="plate plate-quiet enters">
        <p className="plate-kicker">维度变化</p>
        <div className="grid-auto">
          {Object.entries(comparison.dimension_changes).map(([dimension,change])=>
            <dl className="readout" key={dimension}><dt>{dimension.replaceAll("_"," ")}</dt><dd>{change}</dd></dl>)}
        </div>
      </section>}

      <section className="plate plate-quiet enters">
        <p className="plate-kicker">复验读数</p>
        <div className="grid-auto">
          <dl className="readout"><dt>已解决问题</dt><dd>{Object.values(comparison.dimension_changes).filter(value=>value==="IMPROVED").length} 项</dd></dl>
          <dl className="readout"><dt>仍未解决</dt><dd>{Object.values(comparison.dimension_changes).filter(value=>value!=="IMPROVED").length} 项</dd></dl>
          <dl className="readout"><dt>表面完成但复验失败</dt><dd>{Object.values(comparison.dimension_changes).filter(value=>value==="REGRESSED").length} 项</dd></dl>
          <dl className="readout"><dt>证据等级变化</dt><dd>{comparison.comparable?"同标准可比":"标准已漂移"}</dd></dl>
        </div>
        <p style={{ marginTop: 18 }}>{comparison.comparable?"核心任务与标准版本保持一致。":"新标准结果必须作为补充结果单独展示。"}只有重新执行后证据等级下降的维度才列入，不用修改说明代替测试。</p>
      </section>

      {comparison.new_risks.length>0&&<section className="plate enters">
        <p className="plate-kicker">{t("New risks")}</p>
        <p role="alert">{comparison.new_risks.join(" · ")}</p>
      </section>}
    </>}
  </main>;
}
