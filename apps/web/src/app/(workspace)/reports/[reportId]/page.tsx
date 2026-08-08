"use client";

import { use, useEffect, useState } from "react";
import { browserApi, type Report } from "../../../../lib/api-client";
import { EvidenceChain } from "../../../../components/evidence/EvidenceChain";
import { PageHeader, StatusPill } from "../../../../components/shell/AppShell";
import { useI18n } from "../../../../components/i18n/LocaleProvider";

function readableBlock(value: string): string {
  if (value.startsWith("finding_downgraded:")) return "部分结论因证据不足已降级";
  if (value.startsWith("finding_rejected:")) return "部分结论未通过证据审计";
  if (value.startsWith("finding_needs_more_evidence:")) return "部分结论仍需补充证据";
  return value.replaceAll("_", " ");
}

export default function ReportPage({ params }: { params: Promise<{ reportId: string }> }) {
  const { t } = useI18n();
  const { reportId } = use(params); const [report, setReport] = useState<Report>(); const [error, setError] = useState<string>();
  useEffect(() => { void browserApi().getReport(reportId).then(setReport).catch(cause => setError(cause.message)); }, [reportId]);
  const readableBlocks = [...new Set((report?.key_contradictions??report?.blocking_reasons??[]).map(readableBlock))];
  return <main className="workspace-main">
    <PageHeader eyebrow={t("Decision / {id}", { id: reportId.slice(0,8) })} title={t("A verdict with receipts.")} description={t("Rules determine the grade and hard blocks. Explanation makes it readable; Evidence makes it accountable.")} action={report && <StatusPill value={report.recommendation} />} />
    {error && <p role="alert">{error}</p>}
    {report && <>
      <section className="plate enters">
        <p className="plate-kicker">四维证据等级</p>
        <div className="grid-auto">
          {Object.entries(report.dimension_results??Object.fromEntries(Object.entries(report.dimension_grades).map(([key,grade])=>[key,{grade,evidence_confidence:"—",change:"NO_BASELINE"}]))).map(([dimension,result],index)=>
            <dl className="readout" key={dimension}>
              <dt>0{index+1} / {dimension.replaceAll("_"," ")}</dt>
              <dd>
                {result.grade.replaceAll("_"," ")}
                <span className="bearing" style={{ marginTop: 4 }}>{t("Evidence {level} · {change}",{level:result.evidence_confidence,change:result.change.replaceAll("_"," ")})}</span>
              </dd>
            </dl>
          )}
        </div>
      </section>

      {report.geo_trend && <section className="plate plate-quiet enters">
        <p className="plate-kicker">{t("Time / region trend")}</p>
        <h2>{report.geo_trend.signal}</h2>
        <p>{t("Region {region} · as of {date}",{region:report.geo_trend.region??t("Not established"),date:report.geo_trend.as_of??t("Not established")})}</p>
      </section>}

      <section className="plate enters">
        <p className="plate-kicker">阶段结论</p>
        <h2>{report.recommendation.replaceAll("_"," ")}</h2>
        <p>建议由规则与证据等级决定，不输出虚构的爆款概率。</p>
        <div className="grid-auto" style={{ marginTop: 24 }}>
          <dl className="readout"><dt>关键矛盾 / 最大风险</dt><dd>{readableBlocks[0]??"尚无硬阻塞"}</dd></dl>
          <dl className="readout"><dt>最大机会</dt><dd>{Object.entries(report.dimension_results??{}).sort(([,a],[,b])=>b.grade.localeCompare(a.grade))[0]?.[0]?.replaceAll("_"," ")??"待建立"}</dd></dl>
          <dl className="readout"><dt>信息缺口</dt><dd>{report.information_gaps?.length??0} 项待验证</dd></dl>
        </div>
      </section>

      <section className="plate plate-quiet enters">
        <p className="plate-kicker">{t("Next validation cycle")}</p>
        <h2>下一轮最值得执行的行动</h2>
        <ol>{(report.action_links??report.action_items.map(action=>({action,dimension:null,evidence_ids:[]}))).slice(0,3).map(item=><li key={item.action}>{item.action}{item.dimension&&<span className="bearing"> {item.dimension.replaceAll("_"," ")} · {item.evidence_ids.length} evidence</span>}</li>)}</ol>
        {readableBlocks.length>0 && <p role="alert">{readableBlocks.join(" · ")}</p>}
      </section>

      <section className="plate plate-quiet enters">
        <p className="plate-kicker">证据校准 · {report.calibration_results?.length??0} 项</p>
        <h2>被降级或驳回的结论</h2>
        {report.calibration_results?.length
          ? <ul className="record-list">{report.calibration_results.map(item=><li key={item.finding_id}><span><strong>{item.decision}</strong><span className="bearing">{item.reason}</span></span><span className="bearing">{item.finding_id.slice(0,8)}</span></li>)}</ul>
          : <p>本轮没有需要展示的降级或驳回记录。</p>}
      </section>

      <section className="plate plate-quiet enters">
        <p className="plate-kicker">{t("Lineage")} · {t("{count} links", { count: report.evidence_chain.length })}</p>
        <h2>{t("Finding → Evidence")}</h2>
        <div style={{ marginTop: 18 }}><EvidenceChain items={report.evidence_chain}/></div>
      </section>

      <section className="plate enters">
        <p className="plate-kicker">版本复验</p>
        <h2>修改产品后，按同一任务再检查一次。</h2>
        <p>后续版本会读取本轮未解决问题并重新获取政策、价格、平台规则和行业趋势，不直接复制旧结论。</p>
        <p style={{ marginTop: 20 }}><a className="button" href={`/projects/${report.project_id}/new-evaluation`}>提交新版本并复验</a></p>
      </section>
    </>}
  </main>;
}
