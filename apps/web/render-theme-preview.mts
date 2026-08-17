/**
 * 把归档的 Compass.tsx 真实渲染成静态 HTML 预览。
 *
 * 目的不是"画一张好看的图"，而是证明这份备份**真的能渲染** ——
 * 手抄一份 SVG 只能证明我会抄，跑通真组件才能证明备份完整。
 *
 * 必须放在 apps/web 下：ESM 从**脚本自身位置**解析裸包名，
 * 放在 docs/ 里会 ERR_MODULE_NOT_FOUND（react 解析不到）。
 * 产物仍写回 docs/design/themes/01-astrolabe/。
 *
 * 用法：
 *   cd apps/web && node --experimental-strip-types render-theme-preview.mts
 */

import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

// tsx 的 --tsconfig 不作用于被 import 的文件，Compass.tsx 会按 classic JSX
// 编译成 React.createElement 而报 "React is not defined"。
// 故显式用 workspace 的 typescript 以 automatic runtime 转译后再动态载入。
// 落盘位置必须在 apps/web 内，否则临时目录解析不到 react/jsx-runtime。

const HERE = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../docs/design/themes/01-astrolabe",
);

const compassSrc = readFileSync(join(HERE, "Compass.tsx"), "utf8");
const transpiled = ts.transpileModule(compassSrc, {
  compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX,
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
  },
}).outputText;

const tmp = mkdtempSync(join(dirname(fileURLToPath(import.meta.url)), ".theme-preview-"));
const compiled = join(tmp, "Compass.mjs");
writeFileSync(compiled, transpiled, "utf8");

const { Compass } = await import(`file://${compiled.replace(/\\/g, "/")}`);

type CompassSector = {
  key: string;
  code: string;
  name: string;
  filled: number;
  total: number;
};
type CompassNeedle = { key: string; name: string; status: string; evidence: number };

// 参考图里的真实取值：四扇区、0 证据、全部 IDLE、扇区 II 选中
const sectors: CompassSector[] = [
  { key: "product", code: "I", name: "产品材料", filled: 0, total: 3 },
  { key: "team", code: "II", name: "团队信息", filled: 0, total: 2 },
  { key: "users", code: "III", name: "用户与经营", filled: 0, total: 3 },
  { key: "geo", code: "IV", name: "时间与地域", filled: 0, total: 3 },
];

const needles: CompassNeedle[] = [
  { key: "director", name: "爆款预测主管", status: "IDLE", evidence: 0 },
  { key: "product", name: "产品与团队专家", status: "IDLE", evidence: 0 },
  { key: "user", name: "用户共创 Agent", status: "IDLE", evidence: 0 },
  { key: "business", name: "投资与商业 Agent", status: "IDLE", evidence: 0 },
  { key: "geo", name: "时间地域 Agent", status: "IDLE", evidence: 0 },
  { key: "calibration", name: "证据校准 Agent", status: "IDLE", evidence: 0 },
];

const inscription = [
  "所有判断保留证据",
  "敏感信息不进入报告",
  "代码仓库只读",
  "支持同标准复验",
  "高风险操作人工确认",
  "不把猜测当事实",
];

const svg = renderToStaticMarkup(
  createElement(Compass, {
    sectors,
    needles,
    notch: 0,
    notches: 40,
    activeSector: 1,
    onSelectSector: () => {},
    inscription,
  }),
);

const css = readFileSync(join(HERE, "globals.head.css"), "utf8");

const html = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>主题归档 · 01 星盘 / 航海罗经</title>
<style>
${css}
</style>
<style>
  /* 预览专用：仅约束画布尺寸，不覆盖任何主题样式 */
  body { display: grid; place-items: start center; padding: 32px 16px 64px; }
  .preview-frame { width: min(92vw, 860px); aspect-ratio: 1; }
  .preview-frame svg { width: 100%; height: 100%; }
  .preview-note {
    max-width: 860px; margin: 0 auto 24px;
    font-size: 13px; line-height: 1.7; opacity: 0.75;
  }
</style>
</head>
<body>
  <p class="preview-note">
    主题归档预览 · 01 星盘 / 航海罗经。由归档的 <code>Compass.tsx</code> +
    <code>globals.head.css</code> 经 react-dom/server 静态渲染而成，
    未手工修改任何一行 SVG。数据为参考图取值：0 证据、全部 IDLE、扇区 II 选中。
  </p>
  <div class="preview-frame">${svg}</div>
</body>
</html>
`;

const out = join(HERE, "preview.html");
writeFileSync(out, html, "utf8");
rmSync(tmp, { recursive: true, force: true });
console.log(`已写出 ${out}`);
console.log(`SVG ${svg.length} 字符 / CSS ${css.length} 字符`);
