"use client";

import {
  buildCalibrationState,
  buildDimensionStates,
  buildSectorStates,
  notchFromEvidence,
  type SectorState,
} from "../../lib/wheel-state";
import { AGENT_GLYPHS } from "../../lib/agent-glyphs";
import type { AgentTeamsRun } from "../../lib/api-client";

/**
 * EvaluationWheel — 明亮实体评审仪器。
 * 内环 = 用户确认的客观资料（可点）。
 * 外环 = 四个评审视角（PRODUCT_IMPLEMENTATION / USER_USAGE /
 *        BUSINESS_INVESTMENT / GEO_POLICY_TREND）。
 * 中心 = 项目名 / 版本 / 当前阶段 / 当前 CTA（由外层渲染覆盖）。
 * 最外 = Evidence Auditor 校准光环（状态 / 完整度 / 已落库证据数）。
 * 盘面只被真实证据、真实状态推动；没有事件，盘面静止。
 */

const R = {
  rim: 464,
  haloOuter: 448,
  haloInner: 388,
  calibration: 412,
  tickOuter: 372,
  tickMajorInner: 352,
  tickMinorInner: 360,
  sectorOuter: 252,
  sectorInner: 158,
  core: 136,
} as const;

const round = (value: number) => Math.round(value * 100) / 100;

function polar(radius: number, deg: number): [number, number] {
  const rad = ((deg - 90) * Math.PI) / 180;
  return [round(radius * Math.cos(rad)), round(radius * Math.sin(rad))];
}

function arcPath(r0: number, r1: number, a0: number, a1: number): string {
  const [x0o, y0o] = polar(r1, a0);
  const [x1o, y1o] = polar(r1, a1);
  const [x1i, y1i] = polar(r0, a1);
  const [x0i, y0i] = polar(r0, a0);
  const large = Math.abs(a1 - a0) > 180 ? 1 : 0;
  return `M ${x0o},${y0o} A ${r1},${r1} 0 ${large},1 ${x1o},${y1o} L ${x1i},${y1i} A ${r0},${r0} 0 ${large},0 ${x0i},${y0i} Z`;
}

export type EvaluationWheelProps = {
  sectors: SectorState[];
  team?: AgentTeamsRun;
  fields?: Record<string, string>;
  notch?: number;
  activeSector?: number;
  ambient?: boolean;
  needsAttention?: boolean;
  showSectorCounts?: boolean;
  onSelectSector?: (index: number) => void;
};

const STATUS_TONE: Record<string, string> = {
  RUNNING: "running",
  LEASED: "running",
  NEEDS_INPUT: "attention",
  COMPLETED: "completed",
  SUCCEEDED: "completed",
  VALIDATED: "completed",
};

export function EvaluationWheel({
  sectors,
  team,
  fields,
  notch,
  activeSector = 0,
  ambient = false,
  needsAttention = false,
  showSectorCounts = true,
  onSelectSector,
}: EvaluationWheelProps) {
  const dimensions = buildDimensionStates(team);
  const calibration = buildCalibrationState(team);
  const driveNotch = notch ?? notchFromEvidence(calibration.evidenceTotal);
  const notchAngle = 360 / 32;
  const reteAngle = driveNotch * notchAngle;
  const materAngle = -reteAngle * 0.5;
  const materVisualAngle = materAngle + (onSelectSector ? activeSector * 90 : 0);
  const indexAngle = reteAngle - (onSelectSector ? activeSector * 90 : 0);
  const sectorStates = sectors.length ? sectors : buildSectorStates(fields ?? {});
  const haloTone = needsAttention || calibration.status === "ATTENTION"
    ? "attention"
    : calibration.status === "CALIBRATED" ? "ok" : "";
  const calibrationStatus = needsAttention ? "ATTENTION" : calibration.status;

  return (
    <svg
      viewBox="-500 -500 1000 1000"
      role="img"
      aria-label="评审转盘"
      className="evaluation-wheel"
      data-ambient={ambient || undefined}
    >
      <title>评审转盘：内环为客观资料，外环为四个评审视角，最外为证据校准光环</title>

      <defs>
        <radialGradient id="wheel-enamel-face" cx="36%" cy="24%" r="82%">
          <stop offset="0%" stopColor="#30302b" />
          <stop offset="38%" stopColor="#171714" />
          <stop offset="76%" stopColor="#0b0b0a" />
          <stop offset="100%" stopColor="#030303" />
        </radialGradient>
        <linearGradient id="wheel-gold-metal" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#6d4208" />
          <stop offset="14%" stopColor="#d7a83e" />
          <stop offset="30%" stopColor="#fff0a8" />
          <stop offset="47%" stopColor="#a96810" />
          <stop offset="63%" stopColor="#f4d475" />
          <stop offset="78%" stopColor="#fff4bc" />
          <stop offset="100%" stopColor="#704208" />
        </linearGradient>
        <radialGradient id="wheel-gold-bloom" cx="32%" cy="24%" r="76%">
          <stop offset="0%" stopColor="#fff4bd" />
          <stop offset="46%" stopColor="#d6a33a" />
          <stop offset="100%" stopColor="#754408" />
        </radialGradient>
        <pattern id="wheel-guilloche" width="24" height="24" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <path d="M -6 12 Q 0 2 6 12 T 18 12 T 30 12" fill="none" stroke="#d6ab55" strokeWidth="0.7" opacity="0.23" />
          <path d="M 12 -6 Q 2 0 12 6 T 12 18 T 12 30" fill="none" stroke="#fff1a5" strokeWidth="0.45" opacity="0.15" />
        </pattern>
        <filter id="wheel-soft-glow" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur stdDeviation="4" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* ── 机身金属环 ── */}
      <circle r={R.rim} className="wheel-rim-metal" stroke="url(#wheel-gold-metal)" />
      <circle r={R.rim - 12} className="plate-ring-strong" />
      <circle r={R.rim - 31} className="wheel-crystal-face" fill="url(#wheel-enamel-face)" />
      <circle r={R.rim - 45} className="wheel-guilloche" fill="url(#wheel-guilloche)" />
      <circle r={R.rim - 7} className="wheel-gold-rim" stroke="url(#wheel-gold-metal)" />

      <g className="wheel-index-ring" transform={`rotate(${indexAngle})`} aria-hidden="true">
        {Array.from({ length: 32 }, (_, i) => {
          const deg = i * notchAngle;
          const [x, y] = polar(292, deg);
          return <circle key={i} cx={x} cy={y} r={i % 4 === 0 ? 5 : 2.8} className="wheel-index-rivet" fill="url(#wheel-gold-bloom)" />;
        })}
      </g>

      {/* ── 校准光环（Evidence Auditor，最外层，非聊天标签） ── */}
      <g aria-hidden="true">
        <circle r={R.haloOuter} className="plate-ring" />
        <circle r={R.haloInner} className="plate-ring" />
        {Array.from({ length: 48 }, (_, i) => {
          const deg = i * 7.5;
          const lit = i < (calibration.evidenceTotal % 49);
          const [x1, y1] = polar(R.calibration, deg);
          const [x2, y2] = polar(calibrationStatus === "IDLE" ? 400 : 424, deg);
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} className="halo-tick" data-lit={lit} />;
        })}
        {calibrationStatus === "CALIBRATING" && <circle r={R.haloOuter} className="halo-sweep" />}
        <text x={0} y={-R.haloOuter - 14} className="halo-label" textAnchor="middle">
          校准环 · EVIDENCE AUDITOR · {calibrationStatus.replaceAll("_", " ")}
        </text>
        <text x={0} y={R.haloOuter + 16} className="halo-label" data-tone={haloTone} textAnchor="middle">
          {calibration.evidenceTotal} 份证据落库 · 完整度 {Math.min(100, Math.round((driveNotch / 32) * 100))}% ·{" "}
          {calibration.needsHumanReview > 0 ? `${calibration.needsHumanReview} 项需人工确认` : "无需人工确认"}
        </text>
      </g>

      {/* ── 刻度环（Mater）：棘轮驱动，逆向半速 ── */}
      <g className="mater-ring" transform={`rotate(${materVisualAngle})`}>
        {Array.from({ length: 128 }, (_, i) => {
          const deg = i * 2.8125;
          const major = i % 4 === 0;
          const [x1, y1] = polar(R.tickOuter, deg);
          const [x2, y2] = polar(major ? R.tickMajorInner : R.tickMinorInner, deg);
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} className={major ? "tick-major" : "tick-minor"} />;
        })}
        {Array.from({ length: 8 }, (_, i) => {
          const deg = i * 45;
          const [x, y] = polar(336, deg);
          return (
            <text key={deg} x={x} y={y} className="degree-label" transform={`rotate(${-materVisualAngle} ${x} ${y})`}>
              {String(deg).padStart(3, "0")}
            </text>
          );
        })}
      </g>

      {/* ── 内环：客观资料扇区 ── */}
      <g>
        {sectorStates.map((sector, i) => {
          const span = 360 / sectorStates.length;
          const mid = i * span;
          const a0 = mid - span / 2 + 2;
          const a1 = mid + span / 2 - 2;
          const [cx, cy] = polar((R.sectorOuter + R.sectorInner) / 2, mid);
          const complete = sector.total > 0 && sector.filled === sector.total;
          const active = i === activeSector;
          return (
            <g
              key={sector.key}
              className="wheel-sector"
              data-active={active}
              data-complete={complete}
              role={onSelectSector ? "button" : undefined}
              tabIndex={onSelectSector ? 0 : undefined}
              aria-label={onSelectSector ? `${sector.name}，已填 ${sector.filled} / ${sector.total}` : undefined}
              aria-pressed={onSelectSector ? active : undefined}
              onClick={onSelectSector ? () => onSelectSector(i) : undefined}
              onKeyDown={onSelectSector ? (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelectSector(i);
                }
              } : undefined}
            >
              <path d={arcPath(R.sectorInner, R.sectorOuter, a0, a1)} className="wheel-sector-face" />
              <text x={cx} y={cy - 22} className="sector-num">
                {sector.code}
              </text>
              <text x={cx} y={cy + 2} className="wheel-sector-name">
                {sector.name}
              </text>
              <text x={cx} y={cy + 24} className="sector-fill">
                {showSectorCounts ? `${sector.filled} / ${sector.total}` : "FROZEN"}
              </text>
            </g>
          );
        })}
      </g>

      {/* ── 外环：四个评审视角（Rete），棘轮步进 ── */}
      <g className="rete" transform={`rotate(${reteAngle})`}>
        {dimensions.map((dimension, i) => {
          const deg = 90 * i;
          const [tx, ty] = polar(385, deg);
          const tone = STATUS_TONE[dimension.status] ?? "idle";
          const labelWidth = dimension.name.length > 6 ? 172 : 132;
          return (
            <g key={dimension.code} className="wheel-dimension" data-tone={tone}>
              <g transform={`rotate(${deg})`}>
                <line x1={0} y1={-R.sectorOuter} x2={0} y2={-290} className="dimension-stem" />
                <g transform={`translate(0 ${-272})`}>
                  <circle r={17} className="dimension-socket" />
                  <path d={AGENT_GLYPHS[dimension.agent] ?? AGENT_GLYPHS.default} className="dimension-glyph" />
                </g>
              </g>
              <rect
                x={tx - labelWidth / 2}
                y={ty - 26}
                width={labelWidth}
                height={52}
                rx={20}
                className="dimension-label-bg"
                transform={`rotate(${-reteAngle} ${tx} ${ty})`}
              />
              <text x={tx} y={ty - 3} className="dimension-name" transform={`rotate(${-reteAngle} ${tx} ${ty})`}>
                {dimension.name}
              </text>
              <text x={tx} y={ty + 16} className="dimension-read" transform={`rotate(${-reteAngle} ${tx} ${ty + 16})`}>
                {dimension.evidence ? `${dimension.evidence} 证据` : dimension.status.replaceAll("_", " ")}
              </text>
            </g>
          );
        })}
      </g>

      {/* ── 中心底盘 ── */}
      <circle r={R.core} className="wheel-core-plate" />
      <circle r={R.core - 12} className="plate-ring" />

      {/* ── 环境旋转层：仅首页待机，极慢线性 ── */}
      {ambient && (
        <g className="ambient-spin" aria-hidden="true">
          <circle r={R.tickMajorInner - 8} className="ambient-orbit ambient-orbit-primary" stroke="url(#wheel-gold-metal)" />
          <circle r={R.sectorOuter + 22} className="ambient-orbit ambient-orbit-secondary" />
          {Array.from({ length: 12 }, (_, i) => {
            const [x, y] = polar(418, i * 30);
            return (
              <circle
                key={i}
                cx={x}
                cy={y}
                r={i % 3 === 0 ? 5.5 : 3.2}
                className="wheel-spark"
                style={{ animationDelay: `${i * -0.21}s` }}
              />
            );
          })}
        </g>
      )}
      {ambient && <circle r={R.haloOuter - 9} className="wheel-glint" stroke="url(#wheel-gold-metal)" aria-hidden="true" />}
    </svg>
  );
}
