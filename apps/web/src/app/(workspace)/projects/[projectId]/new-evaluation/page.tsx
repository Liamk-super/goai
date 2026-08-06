"use client";

import { use, useMemo, useState } from "react";
import { browserApi } from "../../../../../lib/api-client";
import type { GapQuestion } from "../../../../../components/profile/ProfileConfirmation";
import { StatusPill } from "../../../../../components/shell/AppShell";
import { extractPdfText, fitModelContent } from "../../../../../lib/pdf-text";

type Fields = Record<string, string>;
type IntakeSection = { id: string; title: string; subtitle: string; fields: { key: string; label: string; placeholder: string }[] };

const sections: IntakeSection[] = [
  { id: "product", title: "产品材料", subtitle: "问题、核心功能与可检查入口", fields: [
    { key: "problem", label: "产品解决什么问题", placeholder: "描述真实痛点和发生场景" },
    { key: "core_features", label: "核心功能", placeholder: "列出 1—3 个核心能力" },
    { key: "inspectable_materials", label: "产品网址 / 仓库地址", placeholder: "https://…" },
  ] },
  { id: "team", title: "团队信息", subtitle: "角色、能力与当前交付边界", fields: [
    { key: "team", label: "团队与分工", placeholder: "核心成员、负责领域和投入方式" },
    { key: "stage", label: "当前产品阶段", placeholder: "想法 / 原型 / 内测 / 已上线" },
  ] },
  { id: "market", title: "用户与经营数据", subtitle: "使用者、付费者与验证目标", fields: [
    { key: "target_user", label: "目标用户 / 使用者", placeholder: "谁在什么情况下使用" },
    { key: "payer", label: "付费者", placeholder: "谁做购买决策并承担成本" },
    { key: "validation_goal", label: "本轮最想验证什么", placeholder: "本轮希望支持哪个投入决策" },
  ] },
  { id: "geo", title: "时间与地域", subtitle: "市场边界、政策与信息时效", fields: [
    { key: "region", label: "目标国家或地区", placeholder: "例如：中国香港 / 东南亚" },
    { key: "timing", label: "计划时间与窗口", placeholder: "上线时间、窗口期或截止日期" },
  ] },
];

const required = ["problem", "core_features", "target_user", "payer", "stage", "validation_goal", "region", "inspectable_materials"];

export default function NewEvaluationPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const [active, setActive] = useState(0);
  const [fields, setFields] = useState<Fields>({});
  const [rawContent, setRawContent] = useState("");
  const [file, setFile] = useState<File>();
  const [fileText, setFileText] = useState("");
  const [fileStatus, setFileStatus] = useState("");
  const [fileParsing, setFileParsing] = useState(false);
  const [externalConsent, setExternalConsent] = useState(false);
  const [questions, setQuestions] = useState<GapQuestion[]>([]);
  const [correlationId, setCorrelationId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [versionLabel, setVersionLabel] = useState("V1");
  const [runId, setRunId] = useState("");
  const [phase, setPhase] = useState<"collect" | "review" | "questions" | "planned">("collect");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const completion = useMemo(() => Math.round(required.filter(key => fields[key]?.trim()).length / required.length * 100), [fields]);

  function update(key: string, value: string) { setFields(current => ({ ...current, [key]: value })); }

  async function selectFile(selected?: File) {
    setFile(selected); setFileText(""); setFileStatus(""); setError(undefined);
    if (!selected || selected.type !== "application/pdf") return;
    setFileParsing(true); setFileStatus("正在浏览器本地读取 PDF…");
    try {
      const result = await extractPdfText(selected);
      setFileText(result.text);
      setFileStatus(`已在本地读取 ${result.pageCount} 页、${result.characterCount.toLocaleString()} 字符${result.truncated ? "；送往模型的内容按 3 万字符上限保留首尾重点" : ""}。`);
    } catch (cause) {
      setFileStatus(cause instanceof Error ? cause.message : "PDF 文字读取失败，请粘贴文字后继续。");
    } finally { setFileParsing(false); }
  }

  async function prepareDraft() {
    setBusy(true); setError(undefined);
    try {
      const api = browserApi(); let merged = { ...fields };
      const material = fitModelContent([rawContent.trim(), fileText].filter(Boolean).join("\n\n")).text;
      if (material) {
        if (!externalConsent) throw new Error("使用 AI 提取前，请明确同意将这段材料发送到已配置的模型服务。");
        const extraction = await api.extractIntake(material);
        merged = Object.fromEntries(Object.entries({ ...extraction.extracted_fields, ...merged }).map(([key, value]) => [key, value ?? ""]));
        setFields(merged);
      }
      setPhase("review");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "资料提取失败";
      setError(message === "the configured model could not produce a valid extraction draft"
        ? "模型已响应，但结果不符合产品画像格式。本次调用已停止，请勿重复提交；请先由管理员核对供应商返回格式与 usage 回执。"
        : message);
    } finally { setBusy(false); }
  }

  async function commitProfile() {
    setBusy(true); setError(undefined);
    try {
      const api = browserApi();
      let activeVersion = versionId;
      if (!activeVersion) {
        const existing = await api.listRuns(projectId);
        const nextLabel = `V${existing.items.length + 1}`;
        setVersionLabel(nextLabel);
        activeVersion = (await api.createVersion(projectId, nextLabel)).product_version_id;
      }
      setVersionId(activeVersion);
      const snapshot = new File([JSON.stringify({ sections: fields, raw_content: rawContent || null }, null, 2)], "product-intake.json", { type: "application/json" });
      await api.uploadMaterial(activeVersion, snapshot);
      if (file) await api.uploadMaterial(activeVersion, file);
      const gaps = await api.gapQuestions(activeVersion); setCorrelationId(gaps.correlation_id); setQuestions(gaps.questions);
      const answerable = Object.fromEntries(gaps.questions.filter(question => fields[question.field]?.trim()).map(question => [question.field, fields[question.field].trim()]));
      if (Object.keys(answerable).length) await api.answerGaps(activeVersion, gaps.correlation_id, answerable);
      const unresolved = gaps.questions.filter(question => !answerable[question.field]);
      setQuestions(unresolved);
      if (unresolved.length) { setPhase("questions"); return; }
      await api.confirmProfile(activeVersion);
      const run = await api.plan(activeVersion); setRunId(run.run_id); setPhase("planned");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "产品画像确认失败"); } finally { setBusy(false); }
  }

  async function answerAndPlan() {
    setBusy(true); setError(undefined);
    try {
      const answers = Object.fromEntries(questions.map(question => [question.field, fields[question.field]?.trim() || "unknown"]));
      await browserApi().answerGaps(versionId, correlationId, answers);
      await browserApi().confirmProfile(versionId);
      const run = await browserApi().plan(versionId); setRunId(run.run_id); setPhase("planned");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "补充信息提交失败"); } finally { setBusy(false); }
  }

  return <main className="intake-studio">
    <header className="intake-header"><div><p className="eyebrow">新版本 · 产品档案</p><h1>先让系统理解产品，<br />再开始评审。</h1></div><div className="intake-meter"><strong>{completion}%</strong><span>核心资料完整度</span><i><b style={{ width: `${completion}%` }} /></i></div></header>
    <div className="intake-shell">
      <nav className="section-rail" aria-label="资料模块">{sections.map((section, index) => {
        const filled = section.fields.filter(field => fields[field.key]?.trim()).length;
        return <button key={section.id} className={active === index ? "active" : ""} onClick={() => setActive(index)}><span>0{index + 1}</span><div><strong>{section.title}</strong><small>{filled === section.fields.length ? "待确认" : filled ? "填写中" : "未开始"} · {filled}/{section.fields.length}</small></div></button>;
      })}</nav>
      <section className="intake-canvas">
        {phase === "collect" && <><div className="canvas-heading"><p>{sections[active].subtitle}</p><h2>{sections[active].title}</h2><span>可以逐项填写，也可以粘贴材料让 AI 生成可编辑草稿。</span></div>
          <div className="field-stack">{sections[active].fields.map(field => <label key={field.key}>{field.label}<textarea value={fields[field.key] ?? ""} onChange={event => update(field.key, event.target.value)} placeholder={field.placeholder} /></label>)}</div>
          <details className="free-input" open><summary>自由输入、网址与文件</summary><label>粘贴说明或材料<textarea value={rawContent} onChange={event => setRawContent(event.target.value)} placeholder="粘贴产品介绍、访谈纪要、README 或其他长文本…" /></label><label className="file-drop">补充文件<input type="file" accept=".pdf,.doc,.docx,.txt,image/*" onChange={event => void selectFile(event.target.files?.[0])} /><span>{file?.name ?? "PDF / 文档 / 图片 / 文本，上传后进入私有隔离区"}</span></label>{fileStatus && <p className="file-status" role="status">{fileStatus}</p>}{(rawContent.trim() || fileText) && <><label className="consent-check"><input type="checkbox" checked={externalConsent} onChange={event => setExternalConsent(event.target.checked)} /><span>我确认将粘贴文字和 PDF 中本地提取的文字发送到当前配置的模型服务，仅用于生成待确认草稿。原文件仍走私有隔离上传。</span></label>{active < 3 && <button type="button" className="secondary" onClick={prepareDraft} disabled={busy || fileParsing || !externalConsent}>{busy ? "提取中…" : "直接整理并进入画像确认"}</button>}</>}</details>
          {error && <p role="alert">{error}</p>}<div className="form-actions"><button className="secondary" disabled={active === 0} onClick={() => setActive(value => Math.max(0, value - 1))}>上一类</button>{active < 3 ? <button onClick={() => setActive(value => value + 1)}>{active === 0 ? "下一步：团队信息" : active === 1 ? "下一步：用户与经营" : "下一步：时间与地域"}</button> : <button onClick={prepareDraft} disabled={busy || fileParsing}>{fileParsing ? "读取 PDF…" : busy ? "提取中…" : "用材料整理产品画像"}</button>}</div></>}
        {phase === "review" && <><div className="canvas-heading"><p>人工确认点</p><h2>这是系统理解的产品画像</h2><span>模型提取只是草稿。请逐项修改；点击确认后才会成为本版本的确定事实。</span></div><div className="profile-review">{required.map(key => <label key={key}><span>{sections.flatMap(section => section.fields).find(field => field.key === key)?.label ?? key}</span><textarea value={fields[key] ?? ""} onChange={event => update(key, event.target.value)} placeholder="unknown 也比猜测更可靠" /></label>)}</div>{error && <p role="alert">{error}</p>}<div className="form-actions"><button className="secondary" onClick={() => setPhase("collect")}>返回修改材料</button><button onClick={commitProfile} disabled={busy}>{busy ? "写入档案…" : "确认画像并检查准入"}</button></div></>}
        {phase === "questions" && <><div className="canvas-heading"><p>资料不足 · 本轮只问最关键问题</p><h2>还差 {questions.length} 个判断条件</h2><span>回答“不知道”会被明确记录为 unknown，不会由 AI 自动补造。</span></div><div className="question-stack">{questions.map(question => <label key={question.field}><span>0{question.priority} · {question.question}</span><small>这个问题会影响正式评审的任务边界与证据等级。</small><textarea value={fields[question.field] ?? ""} onChange={event => update(question.field, event.target.value)} placeholder="填写答案，或输入 unknown" /></label>)}</div>{error && <p role="alert">{error}</p>}<button onClick={answerAndPlan} disabled={busy}>{busy ? "建立评审任务…" : "确认补充并开始评审"}</button></>}
        {phase === "planned" && <div className="planned-state"><span>✓</span><p>产品画像已确认</p><h2>{versionLabel} 已形成独立评审 Run</h2><StatusPill value="PLANNED" /><p>下一步会冻结评判标准、任务脚本、模型与预算，再启动 1+5 Agent。页面不会展示内部思维链。</p><a className="button" href={`/runs/${runId}`}>进入 Agent 运行台</a></div>}
      </section>
      <aside className="intake-assistant"><p className="panel-kicker">右侧项目助手</p><h3>{phase === "collect" ? "填写建议" : phase === "review" ? "确认前检查" : phase === "questions" ? "关键追问" : "运行准备"}</h3><p>{phase === "collect" ? "先提供会改变结论的事实。非核心细节可以留空，相关结论会标记为证据不足。" : phase === "review" ? "重点检查目标用户、付费者、阶段、地域和验证目标是否准确。" : phase === "questions" ? "系统不会重复询问已经写入材料的内容。" : "Run Manifest 会固定本轮标准，便于后续版本按同一任务复验。"}</p><div className="assistant-boundary"><strong>信任边界</strong><span>未经确认的内容不写入产品档案</span><span>敏感信息不进入报告</span><span>对外动作与付费操作需人工确认</span></div></aside>
    </div>
  </main>;
}
