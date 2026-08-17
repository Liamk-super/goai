"use client";

import { use, useEffect, useState } from "react";
import { browserApi } from "../../../../../../lib/api-client";
import { PageHeader, StatusPill } from "../../../../../../components/shell/AppShell";
import { useI18n } from "../../../../../../components/i18n/LocaleProvider";

type Comparison={project_id:string;baseline_run_id:string;candidate_run_id:string;comparable:boolean;standard_version:string;supplemental_standard_version?:string|null;baseline_status:string;candidate_status:string;dimension_changes:Record<string,string>;new_risks:string[]};
export default function ComparePage({ params }: { params: Promise<{ projectId: string; runId: string }> }) {
  const { status, t } = useI18n();
  const { projectId, runId } = use(params); const [comparison,setComparison]=useState<Comparison>(); const [error,setError]=useState<string>();
  const [noBaseline,setNoBaseline]=useState(false);
  useEffect(()=>{void browserApi().compare(projectId,runId).then(value=>setComparison(value as unknown as Comparison)).catch(cause=>{
    const message = cause instanceof Error ? cause.message : String(cause);
    if (/prior completed run|baseline/i.test(message)) { setNoBaseline(true); return; }
    setError(message);
  });},[projectId,runId]);
  return <main className="workspace-main">
    <PageHeader eyebrow={t("Version regression")} title={t("Compare two versions")} description={t("Run the evaluation again after making changes to see what was truly resolved and what merely changed wording.")} action={comparison&&<StatusPill value={comparison.comparable?"COMPARABLE":"STANDARD DRIFT"}/>} />
    {noBaseline&&<section className="plate enters">
      <p className="plate-kicker">{t("No comparable version yet")}</p>
      <h2>{t("This is the first version, so there is nothing to compare yet.")}</h2>
      <p style={{ marginTop: 14 }}>
        <a className="button" href={`/projects/${projectId}/new-evaluation`}>{t("Submit new version")}</a>
      </p>
    </section>}
    {error&&<p role="alert">{error}</p>}
    {comparison&&<>
      <div className="grid-auto enters">
        <dl className="readout"><dt>{t("Standard")}</dt><dd>{comparison.standard_version}</dd></dl>
        <dl className="readout"><dt>{t("Baseline")}</dt><dd>{status(comparison.baseline_status)}</dd></dl>
        <dl className="readout"><dt>{t("Candidate")}</dt><dd>{status(comparison.candidate_status)}</dd></dl>
        <dl className="readout"><dt>{t("Result")}</dt><dd>{comparison.comparable?t("Valid"):t("Split")}</dd></dl>
      </div>

      <section className="plate enters">
        <p className="plate-kicker">{t("Two versions")}</p>
        <div className="grid-auto">
          <div>
            <span className="bearing">{t("BASELINE VERSION")}</span>
            <h3>{comparison.baseline_run_id.slice(0,8)}</h3>
            <dl className="readout"><dt>{t("Run ID")}</dt><dd>{comparison.baseline_run_id}</dd></dl>
            <StatusPill value={comparison.baseline_status}/>
          </div>
          <div>
            <span className="bearing">{t("CANDIDATE VERSION")}</span>
            <h3>{comparison.candidate_run_id.slice(0,8)}</h3>
            <dl className="readout"><dt>{t("Run ID")}</dt><dd>{comparison.candidate_run_id}</dd></dl>
            <StatusPill value={comparison.candidate_status}/>
          </div>
        </div>
      </section>

      {Object.keys(comparison.dimension_changes).length>0&&<section className="plate plate-quiet enters">
        <p className="plate-kicker">{t("Dimension changes")}</p>
        <div className="grid-auto">
          {Object.entries(comparison.dimension_changes).map(([dimension,change])=>
            <dl className="readout" key={dimension}><dt>{dimension.replaceAll("_"," ")}</dt><dd>{change}</dd></dl>)}
        </div>
      </section>}

      <section className="plate plate-quiet enters">
        <p className="plate-kicker">{t("Re-evaluation readings")}</p>
        <div className="grid-auto">
          <dl className="readout"><dt>{t("Resolved issues")}</dt><dd>{t("{count} items", { count: Object.values(comparison.dimension_changes).filter(value=>value==="IMPROVED").length })}</dd></dl>
          <dl className="readout"><dt>{t("Still unresolved")}</dt><dd>{t("{count} items", { count: Object.values(comparison.dimension_changes).filter(value=>value!=="IMPROVED").length })}</dd></dl>
          <dl className="readout"><dt>{t("Appeared complete but failed re-evaluation")}</dt><dd>{t("{count} items", { count: Object.values(comparison.dimension_changes).filter(value=>value==="REGRESSED").length })}</dd></dl>
          <dl className="readout"><dt>{t("Evidence-level change")}</dt><dd>{comparison.comparable?t("Comparable under the same standard"):t("Standard drifted")}</dd></dl>
        </div>
        <p style={{ marginTop: 18 }}>{comparison.comparable?t("Core tasks and the standard version remain unchanged."):t("Results under the new standard must be shown separately as supplemental results.")} {t("Only dimensions whose evidence level falls after re-execution are included; change notes never substitute for testing.")}</p>
      </section>

      {comparison.new_risks.length>0&&<section className="plate enters">
        <p className="plate-kicker">{t("New risks")}</p>
        <p role="alert">{comparison.new_risks.join(" · ")}</p>
      </section>}
    </>}
  </main>;
}
