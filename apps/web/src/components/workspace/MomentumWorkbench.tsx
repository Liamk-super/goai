"use client";

import { useMemo, useState } from "react";
import type { AgentTeamsRun, Project, Run } from "../../lib/api-client";
import { Compass, type CompassNeedle, type CompassSector } from "./Compass";

const INTAKE_SECTIONS = [
  ["I", "产品材料", "产品解决的问题、核心功能与可检查材料", 3],
  ["II", "团队信息", "角色、能力边界与交付约束", 2],
  ["III", "用户与经营", "使用者、付费者、数据与商业假设", 3],
  ["IV", "时间与地域", "目标市场、政策、平台规则与时效", 2],
] as const;

const DEFAULT_AGENTS = [
  "势能评审主管",
  "产品与团队专家",
  "用户共创 Agent",
  "投资与商业 Agent",
  "时间地域 Agent",
  "证据校准 Agent",
];

const TRUST_BOUNDARY = [
  "不把猜测当事实",
  "所有判断保留证据",
  "敏感信息不进入报告",
  "代码仓库只读",
  "支持同标准复验",
  "高风险操作人工确认",
];

/** 一整周 = 一次完整评审。走完一周，V1 归档。 */
const NOTCHES_PER_REVOLUTION = 32;

function readable(value: string | null | undefined) {
  return (value ?? "DRAFT").replaceAll("_", " ");
}

export function MomentumWorkbench({
  project,
  runs,
  team,
}: {
  project: Project;
  runs: Run[];
  team?: AgentTeamsRun;
}) {
  const latest = runs[0];
  const [activeSector, setActiveSector] = useState(0);

  const needles = useMemo<CompassNeedle[]>(() => {
    if (!team?.tasks.length) {
      return DEFAULT_AGENTS.map((name, index) => ({
        key: `${name}-${index}`,
        name,
        status: index === 0 && latest ? latest.status : "IDLE",
        evidence: 0,
      }));
    }
    const byAgent = new Map<string, CompassNeedle>();
    for (const item of team.tasks) {
      const code = item.agent_identity_ref.split("@")[0];
      const current = byAgent.get(code);
      byAgent.set(code, {
        key: code,
        name: code === "evaluation-manager" ? "势能评审主管" : code.replaceAll("-", " "),
        status: code === "evaluation-manager" ? (latest?.status ?? item.status) : item.status,
        evidence: (current?.evidence ?? 0) + (item.evidence_count ?? 0),
      });
    }
    return [...byAgent.values()];
  }, [latest, team]);

  /** 已落库的证据总数 —— 唯一让盘面前进的东西。没有证据，盘面不动。 */
  const evidenceCount = useMemo(
    () => needles.reduce((sum, n) => sum + n.evidence, 0),
    [needles],
  );
  const notch = Math.min(evidenceCount, NOTCHES_PER_REVOLUTION);

  const sectors = useMemo<CompassSector[]>(
    () =>
      INTAKE_SECTIONS.map(([code, name, , total]) => ({
        key: name,
        code,
        name,
        filled: runs.length ? total : 0,
        total,
      })),
    [runs.length],
  );

  const version = runs.length ? `V${runs.length}` : "V1 草稿";
  const stage = latest?.current_stage ?? (runs.length ? latest.status : "资料收集");
  const completion = runs.length ? 100 : 0;
  const active = INTAKE_SECTIONS[activeSector];

  const primaryHref = latest
    ? `/runs/${latest.run_id}`
    : `/projects/${project.project_id}/new-evaluation`;
  const primaryLabel =
    latest?.status === "COMPLETED" ? "查看报告与证据" : latest ? "查看 Agent 运行" : "开始补充资料";

  return (
    <section className="binnacle">
      <div className="binnacle-plate">
        <div className="compass">
          <Compass
            sectors={sectors}
            needles={needles}
            notch={notch}
            notches={NOTCHES_PER_REVOLUTION}
            activeSector={activeSector}
            onSelectSector={setActiveSector}
            inscription={TRUST_BOUNDARY}
          />
          <div className="core-read">
            <span className="core-rev">{version}</span>
            <strong className="core-name">{project.name}</strong>
            <span className="core-stage">{readable(stage)}</span>
            <span className="core-figure">
              <b>{completion}</b>
              <i>%</i>
            </span>
            <span className="core-figure-label">证据完整度</span>
            <a className="button core-cta" href={primaryHref}>
              {primaryLabel}
            </a>
          </div>
        </div>
      </div>

      <aside className="binnacle-side" aria-label="项目读数">
        <div className="plate">
          <p className="plate-kicker">当前扇区 · {active[0]}</p>
          <h2>{active[1]}</h2>
          <p>{active[2]}</p>
        </div>

        <div className="plate plate-quiet">
          <p className="plate-kicker">证据推进</p>
          {/* 棘轮刻度条：每一格对应一条真实证据落库。
              没有事件就不亮 —— 这不是进度条动画。 */}
          <div className="detent-bar" aria-label={`已推进 ${notch} / ${NOTCHES_PER_REVOLUTION} 格`}>
            {Array.from({ length: NOTCHES_PER_REVOLUTION }, (_, i) => (
              <i key={i} data-lit={i < notch} />
            ))}
          </div>
          <dl className="readout" style={{ marginTop: 16 }}>
            <dt>NOTCH</dt>
            <dd>
              {String(notch).padStart(2, "0")} / {NOTCHES_PER_REVOLUTION}
            </dd>
          </dl>
          <dl className="readout">
            <dt>EVIDENCE</dt>
            <dd>{evidenceCount}</dd>
          </dl>
          <dl className="readout">
            <dt>SOURCE</dt>
            <dd>PostgreSQL</dd>
          </dl>
        </div>

        <div className="plate plate-quiet">
          <p className="plate-kicker">网盘指针 · 1+5</p>
          <ul className="record-list">
            {needles.map((n) => (
              <li key={n.key}>
                <span>{n.name}</span>
                <span className="status" data-state={n.status.toLowerCase()}>
                  {n.evidence ? `${n.evidence} 证据` : readable(n.status)}
                </span>
              </li>
            ))}
          </ul>
        </div>

        <div className="plate plate-quiet">
          <p className="plate-kicker">版本历史</p>
          {runs.length ? (
            <ul className="record-list">
              {runs.map((run, index) => (
                <li key={run.run_id}>
                  <a href={`/runs/${run.run_id}`}>V{runs.length - index}</a>
                  <span className="status" data-state={run.status.toLowerCase()}>
                    {readable(run.status)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p>第一轮正式评审完成后，会在这里形成不可覆盖的 V1 档案。</p>
          )}
        </div>
      </aside>
    </section>
  );
}
