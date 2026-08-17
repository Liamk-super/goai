"use client";

import {
  buildCalibrationState,
  buildDimensionStates,
  buildSectorStates,
  notchFromEvidence,
  type SectorState,
  type WheelMotionState,
} from "../../lib/wheel-state";
import { AGENT_GLYPHS } from "../../lib/agent-glyphs";
import type { AgentTeamsRun } from "../../lib/api-client";
import { useI18n } from "../i18n/LocaleProvider";

/**
 * EvaluationWheel — 明亮实体评审仪器。
 * 内环 = 用户确认的客观资料（可点）。
 * 外环 = v4 三个领域指针（产品 / 用户 / 商业），旧 Run 仍可只读显示历史第四指针。
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
  sectorOuter: 300,
  sectorInner: 150,
  core: 136,
} as const;

const round = (value: number) => Math.round(value * 100) / 100;

function wheelLabelLines(label: string, maxCharacters: number): string[] {
  const words = label.trim().split(/\s+/u);
  if (words.length === 1) return [label];
  const lines: string[] = [];
  for (const word of words) {
    const current = lines.at(-1);
    if (!current || `${current} ${word}`.length > maxCharacters) lines.push(word);
    else lines[lines.length - 1] = `${current} ${word}`;
  }
  return lines.length > 2 ? [lines[0], lines.slice(1).join(" ")] : lines;
}

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
  motionState?: WheelMotionState;
  needsAttention?: boolean;
  showSectorCounts?: boolean;
  architectureGeneration?: string;
  /** 盘面上方一句人话。不传就不画 —— 盘面默认保持干净。 */
  caption?: string;
  onSelectSector?: (index: number) => void;
};

const STATUS_TONE: Record<string, string> = {
  RUNNING: "running",
  LEASED: "running",
  NEEDS_INPUT: "attention",
  NEEDS_ATTENTION: "attention",
  COMPLETED: "completed",
  SUCCEEDED: "completed",
  VALIDATED: "completed",
};

/** 盘面上不要出现机器状态码。用户读到的必须是人话。 */
export function EvaluationWheel({
  sectors,
  team,
  fields,
  notch,
  activeSector = 0,
  ambient = false,
  motionState = "IDLE",
  needsAttention = false,
  showSectorCounts = true,
  architectureGeneration,
  caption,
  onSelectSector,
}: EvaluationWheelProps) {
  const { t, status } = useI18n();
  const dimensions = buildDimensionStates(
    team,
    architectureGeneration === "supervisor-1p4-v1" ? "v4" : "legacy",
    needsAttention,
  );
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
  const activeMotion = ambient || motionState === "RUNNING";

  return (
    <svg
      viewBox="-500 -500 1000 1000"
      role="img"
      aria-label={t("Prediction wheel")}
      className="evaluation-wheel"
      data-ambient={ambient || undefined}
      data-motion-state={motionState}
    >
      <title>{t("Prediction wheel: project facts inside and four prediction dimensions outside")}</title>

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
        {calibrationStatus === "CALIBRATING" && motionState === "RUNNING" && (
          <circle r={R.haloOuter} className="halo-sweep" />
        )}
        {caption && (
          <text x={0} y={-R.haloOuter - 16} className="halo-label" data-tone={haloTone} textAnchor="middle">
            {caption}
          </text>
        )}
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
          const labelLines = wheelLabelLines(t(sector.name), i % 2 === 1 ? 10 : 20);
          const wrapped = labelLines.length > 1;
          return (
            <g
              key={sector.key}
              className="wheel-sector"
              data-active={active}
              data-complete={complete}
              data-wrapped={wrapped || undefined}
              role={onSelectSector ? "button" : undefined}
              tabIndex={onSelectSector ? 0 : undefined}
              aria-label={onSelectSector ? t("{name}, {filled} / {total} completed", { name: t(sector.name), filled: sector.filled, total: sector.total }) : undefined}
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
              <text x={cx} y={cy - 26} className="sector-num">
                {sector.code}
              </text>
              <text x={cx} y={cy + (wrapped ? -8 : 4)} className="wheel-sector-name">
                {labelLines.map((line, lineIndex) => (
                  <tspan key={line} x={cx} dy={lineIndex === 0 ? 0 : 28}>{line}</tspan>
                ))}
              </text>
              {showSectorCounts && (
                <text x={cx} y={cy + (wrapped ? 50 : 32)} className="sector-fill">
                  {`${sector.filled} / ${sector.total}`}
                </text>
              )}
            </g>
          );
        })}
      </g>

      {/* ── 外环：四个评审视角（Rete），棘轮步进 ── */}
      <g className="rete" transform={`rotate(${reteAngle})`}>
        {dimensions.map((dimension, i) => {
          const deg = 90 * i;
          const tone = STATUS_TONE[dimension.status] ?? "idle";
          const read = dimension.evidence
            ? t(dimension.evidence === 1 ? "1 evidence item" : "{count} evidence items", { count: dimension.evidence })
            : status(dimension.status);
          return (
            <g key={dimension.code} className="wheel-dimension" data-tone={tone}>
              <title>{`${t(dimension.name)} · ${read}`}</title>
              <g transform={`rotate(${deg})`}>
                <line x1={0} y1={-R.sectorOuter - 6} x2={0} y2={-316} className="dimension-stem" />
                <g transform={`translate(0 ${-331})`}>
                  <circle r={17} className="dimension-socket" />
                  <path d={AGENT_GLYPHS[dimension.agent] ?? AGENT_GLYPHS.default} className="dimension-glyph" />
                </g>
              </g>
            </g>
          );
        })}
      </g>

      {/* ── 中心底盘 ── */}
      <circle r={R.core} className="wheel-core-plate" />
      <circle r={R.core - 12} className="plate-ring" />

      {/* ── 环境旋转层：仅首页待机，极慢线性 ── */}
      {activeMotion && (
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
      {activeMotion && <circle r={R.haloOuter - 9} className="wheel-glint" stroke="url(#wheel-gold-metal)" aria-hidden="true" />}
    </svg>
  );
}
