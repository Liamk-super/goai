"use client";

import { useMemo, useState, type CSSProperties } from "react";
import type { AgentTeamsRun, Project, Run } from "../../lib/api-client";
import { StatusPill } from "../shell/AppShell";

const intakeSections = [
  ["产品材料", "产品解决的问题、核心功能与可检查材料"],
  ["团队信息", "角色、能力边界与交付约束"],
  ["用户与经营", "使用者、付费者、数据与商业假设"],
  ["时间与地域", "目标市场、政策、平台规则与时效"],
] as const;

const defaultAgents = [
  "势能评审主管",
  "产品与团队专家",
  "用户共创 Agent",
  "投资与商业 Agent",
  "时间地域 Agent",
  "证据校准 Agent",
];

function readable(value: string | null | undefined) {
  return (value ?? "DRAFT").replaceAll("_", " ");
}

export function MomentumWorkbench({ project, runs, team }: { project: Project; runs: Run[]; team?: AgentTeamsRun }) {
  const latest = runs[0];
  const [activeSection, setActiveSection] = useState(0);
  const [panelOpen, setPanelOpen] = useState(true);
  const agents = useMemo(() => {
    if (!team?.tasks.length) return defaultAgents.map((name, index) => ({ name, status: index === 0 && latest ? latest.status : "IDLE", evidence: 0 }));
    const taskAgents = team.tasks.map(item => ({
      name: item.agent_identity_ref.split("@")[0].replaceAll("-", " "),
      status: item.status,
      evidence: item.evidence_count ?? 0,
    }));
    return [{ name: "势能评审主管", status: latest?.status ?? "IDLE", evidence: 0 }, ...taskAgents].slice(0, 6);
  }, [latest, team]);
  const version = runs.length ? `V${runs.length}` : "V1 草稿";
  const stage = latest?.current_stage ?? (runs.length ? latest.status : "资料收集");
  const completion = runs.length ? 100 : 0;

  return <section className={`momentum-layout ${panelOpen ? "panel-open" : ""}`}>
    <div className="momentum-stage">
      <div className="trust-orbit" aria-label="系统信任边界">
        {[
          "不把猜测当事实", "所有判断保留证据", "敏感信息不进入报告", "代码仓库只读", "支持同标准复验", "高风险操作人工确认",
        ].map((label, index) => <span key={label} style={{ "--i": index } as CSSProperties}>{label}</span>)}
      </div>
      <div className="agent-orbit" aria-label="Agent 运行状态">
        {agents.map((agent, index) => <button key={`${agent.name}-${index}`} className={`agent-node status-${agent.status.toLowerCase()}`} style={{ "--i": index } as CSSProperties} onClick={() => setPanelOpen(true)}>
          <i /><strong>{agent.name}</strong><small>{readable(agent.status)} · {agent.evidence} 证据</small>
        </button>)}
      </div>
      <div className="intake-orbit" aria-label="四类资料">
        {intakeSections.map(([title], index) => <button key={title} className={activeSection === index ? "active" : ""} style={{ "--i": index } as CSSProperties} onClick={() => { setActiveSection(index); setPanelOpen(true); }}>
          <span>0{index + 1}</span><strong>{title}</strong><small>{runs.length ? "已归档" : "未开始"}</small>
        </button>)}
      </div>
      <div className="project-core">
        <span className="core-version">{version}</span>
        <h1>{project.name}</h1>
        <p>{readable(stage)}</p>
        <div className="completion"><i style={{ width: `${completion}%` }} /></div>
        <small>资料完整度 {completion}%</small>
        <a className="button core-action" href={latest ? `/runs/${latest.run_id}` : `/projects/${project.project_id}/new-evaluation`}>
          {latest?.status === "COMPLETED" ? "查看报告与证据" : latest ? "查看 Agent 运行" : "开始补充资料"}
        </a>
      </div>
    </div>
    <aside className="context-drawer" aria-label="项目上下文面板">
      <button className="drawer-close" aria-label="关闭面板" onClick={() => setPanelOpen(false)}>×</button>
      <p className="panel-kicker">项目助手 · {runs.length ? "运行阶段" : "资料阶段"}</p>
      <h2>{intakeSections[activeSection][0]}</h2>
      <p>{intakeSections[activeSection][1]}</p>
      {!runs.length ? <>
        <div className="drawer-note"><strong>为什么需要它？</strong><span>这部分信息会影响评审任务的边界、证据要求和后续追问。</span></div>
        <a className="button" href={`/projects/${project.project_id}/new-evaluation?section=${activeSection}`}>打开资料抽屉</a>
      </> : <>
        <div className="drawer-note"><strong>当前真实状态</strong><span>运行与 Agent 状态来自 PostgreSQL / AgentTeams 投影，不展示模型私密推理。</span></div>
        {latest && <><StatusPill value={latest.status} /><p className="mono-note">Run {latest.run_id.slice(0, 12)}</p></>}
      </>}
      <div className="drawer-history"><h3>版本历史</h3>{runs.length ? runs.map((run, index) => <a key={run.run_id} href={`/runs/${run.run_id}`}><span>V{runs.length - index}</span><strong>{readable(run.status)}</strong></a>) : <p>第一轮正式评审完成后会在这里形成不可覆盖的 V1 档案。</p>}</div>
    </aside>
  </section>;
}
