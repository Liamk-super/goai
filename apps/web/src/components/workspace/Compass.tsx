"use client";

import { useI18n } from "../i18n/LocaleProvider";

/**
 * Compass — 航海罗经 / 星盘
 *
 * 结构（沿用星盘的部件语义，换成航海罗经的视觉语言）：
 *   MATER  母盘   固定，边缘刻 360° 与 32 分罗经点
 *   TYMPAN 承盘   本项目的固定参照系，恒向线（rhumb lines）
 *   RETE   网盘   1+5 Agent 判断层，可转动，各自有指针
 *   RULE   尺     版本对比尺
 *
 * 转动机构：瑞士铁路时钟式棘轮定格。
 *   走到位 → 停 → 等下一个真实事件 → 再走一格。
 *   绝不匀速无限旋转（那是 loading spinner）。
 *   每一格推进 = 一条证据落库。没有事件，盘面不动。
 *
 * 坐标系：viewBox -500..500，所有半径按此比例，容器用 aspect-ratio 自适应。
 * 旋转用 SVG transform **attribute** 驱动，不用 CSS transform
 * （CSS transform 在 SVG <g> 上会与 presentation attribute 冲突）。
 */

const R = {
  rim: 468,
  inscription: 452,
  pointRing: 430,
  tickOuter: 424,
  tickMajorInner: 400,
  tickMinorInner: 411,
  degree: 382,
  materInner: 358,
  needleLead: 330,
  needle: 286,
  sectorOuter: 236,
  sectorInner: 152,
  core: 138,
} as const;

/** 32 分罗经点。基点用全称，其余用缩写 —— 航海仪器的标准刻法。 */
const COMPASS_POINTS = [
  "N", "NbE", "NNE", "NEbN", "NE", "NEbE", "ENE", "EbN",
  "E", "EbS", "ESE", "SEbE", "SE", "SEbS", "SSE", "SbE",
  "S", "SbW", "SSW", "SWbS", "SW", "SWbW", "WSW", "WbS",
  "W", "WbN", "WNW", "NWbW", "NW", "NWbN", "NNW", "NbW",
] as const;

const CARDINALS = new Set(["N", "E", "S", "W"]);

/** 六种可辨识的指针剪影。历史上星盘指针形态多样：星/叶/手/球/蛇/矛。
 *  用户不读文字也能认出是哪个 Agent。尖端在 (0,0)，朝 -y。 */
export const NEEDLE_SHAPES: Record<string, string> = {
  star: "M0,-25 L4.9,-8.2 L21,-8.2 L8,2 L13,18.6 L0,8.4 L-13,18.6 L-8,2 L-21,-8.2 L-4.9,-8.2 Z",
  leaf: "M0,-26 C9.6,-13.4 11.5,-1 0,16 C-11.5,-1 -9.6,-13.4 0,-26 Z",
  hand: "M0,-24 L3.4,-10.5 L6.3,-14.4 L7.4,-5.7 L10.5,-8 L9.7,1 C9.7,9.6 5.1,15.3 0,15.3 C-5.1,15.3 -9.7,9.6 -9.7,1 L-10.5,-8 L-7.4,-5.7 L-6.3,-14.4 L-3.4,-10.5 Z",
  orb: "M0,-25 L4.2,-12.4 A 12.9,12.9 0 1,1 -4.2,-12.4 Z",
  serpent: "M0,-26 C8.6,-18.2 -7.6,-10.5 1.9,-2.9 C10.5,3.8 -4.8,9.6 0,16.2 C-8.6,9.6 6.7,3.8 -1.9,-2.9 C-10.5,-10.5 7.6,-18.2 0,-26 Z",
  spear: "M0,-26 L7.3,-8.6 L2.3,-8.6 L2.3,16.2 L-2.3,16.2 L-2.3,-8.6 L-7.3,-8.6 Z",
};

const SHAPE_ORDER = ["star", "leaf", "hand", "orb", "serpent", "spear"] as const;

export function needleShapeFor(index: number): string {
  return NEEDLE_SHAPES[SHAPE_ORDER[index % SHAPE_ORDER.length]];
}

function polar(radius: number, deg: number): [number, number] {
  const rad = ((deg - 90) * Math.PI) / 180;
  return [radius * Math.cos(rad), radius * Math.sin(rad)];
}

function arcPath(r0: number, r1: number, a0: number, a1: number): string {
  const [x0o, y0o] = polar(r1, a0);
  const [x1o, y1o] = polar(r1, a1);
  const [x1i, y1i] = polar(r0, a1);
  const [x0i, y0i] = polar(r0, a0);
  const large = Math.abs(a1 - a0) > 180 ? 1 : 0;
  return `M ${x0o},${y0o} A ${r1},${r1} 0 ${large},1 ${x1o},${y1o} L ${x1i},${y1i} A ${r0},${r0} 0 ${large},0 ${x0i},${y0i} Z`;
}

export type CompassSector = {
  key: string;
  code: string;
  name: string;
  filled: number;
  total: number;
};

export type CompassNeedle = {
  key: string;
  name: string;
  status: string;
  evidence: number;
};

export type CompassProps = {
  sectors: CompassSector[];
  needles: CompassNeedle[];
  /** 已推进的格数。每格 = 一条真实证据。 */
  notch: number;
  /** 一整周的总格数。走完一周 = 一次完整评审。 */
  notches: number;
  activeSector: number;
  onSelectSector: (index: number) => void;
  onSelectNeedle?: (index: number) => void;
  /** 母盘边缘铭文 —— 信任边界，仪器背面语汇 */
  inscription?: string[];
};

export function Compass({
  sectors,
  needles,
  notch,
  notches,
  activeSector,
  onSelectSector,
  onSelectNeedle,
  inscription = [],
}: CompassProps) {
  const { t, status } = useI18n();
  const notchAngle = notches > 0 ? 360 / notches : 9;
  // RETE 顺时针，MATER 逆时针半速 —— 反向视差，这是"转盘"的核心观感
  const reteAngle = notch * notchAngle;
  const materAngle = -reteAngle * 0.5;

  return (
    <svg viewBox="-500 -500 1000 1000" role="img" aria-label={t("Evaluation compass")}>
      <title>{t("Evaluation compass: bearings outside, material sectors in the middle, and 1+5 Agent judgments on the needles")}</title>

      {/* ── 罗经边圈 ── */}
      <circle r={R.rim} className="plate-ring-strong" />
      <circle r={R.rim - 13} className="plate-ring" />

      {/* ── 边缘铭文：信任边界。下半圈翻正，真实罗经的铭文也是分段正立的 ── */}
      {inscription.length > 0 && (
        <g aria-hidden="true">
          {inscription.map((text, i) => {
            const span = 360 / inscription.length;
            const mid = i * span - 90 + span / 2;
            const [x, y] = polar(R.inscription, mid);
            const norm = ((mid % 360) + 360) % 360;
            const rot = norm > 90 && norm < 270 ? mid + 180 : mid;
            return (
              <text
                key={text}
                x={x}
                y={y}
                className="rim-inscription"
                textAnchor="middle"
                dominantBaseline="middle"
                transform={`rotate(${rot} ${x} ${y})`}
              >
                {text}
              </text>
            );
          })}
        </g>
      )}

      {/* ── MATER 母盘：方位刻度环，逆向转动 ── */}
      <g className="mater-ring" transform={`rotate(${materAngle})`}>
        {/* 32 分罗经点 */}
        {COMPASS_POINTS.map((pt, i) => {
          const deg = i * 11.25;
          const [x, y] = polar(R.pointRing, deg);
          const isCardinal = CARDINALS.has(pt);
          // 反向旋转让标签保持正立
          return (
            <text
              key={pt}
              x={x}
              y={y}
              className={`point-label${isCardinal ? " point-label-cardinal" : ""}`}
              transform={`rotate(${-materAngle} ${x} ${y})`}
            >
              {pt}
            </text>
          );
        })}

        {/* 刻度：主刻度 32 格（每罗经点），副刻度 128 格 */}
        {Array.from({ length: 128 }, (_, i) => {
          const deg = i * 2.8125;
          const major = i % 4 === 0;
          const [x1, y1] = polar(R.tickOuter, deg);
          const [x2, y2] = polar(major ? R.tickMajorInner : R.tickMinorInner, deg);
          return (
            <line
              key={i}
              x1={x1}
              y1={y1}
              x2={x2}
              y2={y2}
              className={major ? "tick-major" : "tick-minor"}
            />
          );
        })}

        {/* 度数标注：每 30° */}
        {Array.from({ length: 12 }, (_, i) => {
          const deg = i * 30;
          const [x, y] = polar(R.degree, deg);
          return (
            <text
              key={deg}
              x={x}
              y={y}
              className="degree-label"
              transform={`rotate(${-materAngle} ${x} ${y})`}
            >
              {String(deg).padStart(3, "0")}
            </text>
          );
        })}
      </g>

      {/* ── TYMPAN 承盘：恒向线。航海图上从罗经点辐射的定向线 ── */}
      <g aria-hidden="true">
        <circle r={R.materInner} className="plate-ring" />
        {Array.from({ length: 32 }, (_, i) => {
          const deg = i * 11.25;
          const [x1, y1] = polar(R.core, deg);
          const [x2, y2] = polar(R.materInner, deg);
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} className="rhumb" />;
        })}
        <circle r={R.sectorOuter} className="plate-ring" />
      </g>

      {/* ── 四类资料扇区：可点，落定有棘轮咬合感 ── */}
      <g>
        {sectors.map((s, i) => {
          const span = 360 / sectors.length;
          const mid = i * span;
          const a0 = mid - span / 2 + 2;
          const a1 = mid + span / 2 - 2;
          const [cx, cy] = polar((R.sectorOuter + R.sectorInner) / 2, mid);
          const active = i === activeSector;
          return (
            <g
              key={s.key}
              className="sector"
              data-active={active}
              role="button"
              tabIndex={0}
              aria-label={t("{name}, {filled} / {total} completed", { name: t(s.name), filled: s.filled, total: s.total })}
              aria-pressed={active}
              onClick={() => onSelectSector(i)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelectSector(i);
                }
              }}
            >
              <path d={arcPath(R.sectorInner, R.sectorOuter, a0, a1)} className="sector-face" />
              <text x={cx} y={cy - 20} className="sector-num">
                {s.code}
              </text>
              <text x={cx} y={cy + 4} className="sector-name">
                {s.name}
              </text>
              <text x={cx} y={cy + 26} className="sector-fill">
                {s.filled} / {s.total}
              </text>
            </g>
          );
        })}
      </g>

      {/* ── RETE 网盘：1+5 判断指针，棘轮步进 ── */}
      <g className="rete" transform={`rotate(${reteAngle})`}>
        {needles.map((n, i) => {
          const deg = (360 / Math.max(needles.length, 1)) * i;
          const lead = i === 0;
          const radius = lead ? R.needleLead : R.needle;
          const [tx, ty] = polar(radius + 40, deg);
          const state = n.status.toLowerCase();
          return (
            <g
              key={n.key}
              className="needle"
              data-state={state}
              role="button"
              tabIndex={0}
              aria-label={t("{name}: {status}, {count} evidence items", { name: t(n.name), status: status(n.status), count: n.evidence })}
              onClick={() => onSelectNeedle?.(i)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelectNeedle?.(i);
                }
              }}
            >
              {/* 极坐标层：先转方位角，再沿 -y 推出半径。
                  父层旋转时子层不会各自把它抵消掉。 */}
              <g transform={`rotate(${deg})`}>
                <line x1={0} y1={-R.core} x2={0} y2={-radius + 24} className="tick-minor" />
                <path
                  d={needleShapeFor(i)}
                  className="needle-body"
                  transform={`translate(0 ${-radius})`}
                />
              </g>
              {/* 名称默认隐藏，hover 才显示 —— 盘面保持干净 */}
              <text
                x={tx}
                y={ty}
                className="needle-caption"
                transform={`rotate(${-reteAngle} ${tx} ${ty})`}
              >
                {n.name}
              </text>
              <text
                x={tx}
                y={ty + 15}
                className="needle-caption"
                transform={`rotate(${-reteAngle} ${tx} ${ty + 15})`}
              >
                {t(n.evidence === 1 ? "1 evidence item" : "{count} evidence items", { count: n.evidence })}
              </text>
            </g>
          );
        })}
      </g>

      {/* ── 中心 ── */}
      <circle r={R.core} className="compass-core" />
      <circle r={R.core - 10} className="plate-ring" />
    </svg>
  );
}
