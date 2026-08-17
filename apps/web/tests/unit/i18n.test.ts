import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { hasTranslation, normalizeLocale, translate, translateGapQuestion, translateStatus } from "../../src/lib/i18n.ts";
import { INTAKE_SECTIONS, SOURCE_LABELS } from "../../src/lib/intake-draft.ts";
import { SUPERVISOR_STAGES } from "../../src/lib/supervisor-experience.ts";
import { VOICE_STATUS_TEXT } from "../../src/lib/voice-capture.ts";
import { FACT_SECTORS, JUDGMENT_DIMENSIONS } from "../../src/lib/wheel-state.ts";
import { beadSignal } from "../../src/lib/history-beads.ts";

const testDirectory = dirname(fileURLToPath(import.meta.url));
const sourceDirectory = join(testDirectory, "../../src");

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap(name => {
    const path = join(directory, name);
    return statSync(path).isDirectory() ? sourceFiles(path) : [path];
  });
}

function withoutComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//gu, "").replace(/^\s*\/\/.*$/gmu, "");
}

test("Chinese browser locales resolve to simplified Chinese", () => {
  assert.equal(normalizeLocale("zh-HK"), "zh-CN");
  assert.equal(normalizeLocale("zh-CN"), "zh-CN");
  assert.equal(normalizeLocale("en-US"), "en");
});

test("Chinese translations interpolate values and preserve unknown product data", () => {
  assert.equal(translate("zh-CN", "{count} total", { count: 3 }), "共 3 次");
  assert.equal(translate("zh-CN", "untranslated-api-value"), "untranslated-api-value");
  assert.equal(translate("en", "Projects"), "Projects");
});

test("Chinese product copy consistently presents the hit predictor and prediction flow", () => {
  assert.equal(translate("zh-CN", "LaunchScope"), "爆款预测器");
  assert.equal(translate("zh-CN", "Four-stage evaluation"), "四阶段预测");
  assert.equal(translate("zh-CN", "多维评审"), "多维预测");
  assert.equal(translate("zh-CN", "Evaluation history"), "历史预测");
  assert.equal(translate("zh-CN", "Finding → Evidence"), "结论 → 证据");
  assert.equal(translate("zh-CN", "View technical details"), "查看技术详情");
  assert.equal(translate("zh-CN", "1 evidence item"), "1 条证据");
});

test("the 1+4 roles use product-facing names without Agent suffixes", () => {
  assert.equal(translate("zh-CN", "Prediction project lead"), "项目负责人");
  assert.equal(translate("zh-CN", "Product and team"), "产品经理");
  assert.equal(translate("zh-CN", "User evidence"), "目标用户");
  assert.equal(translate("zh-CN", "Business and investment"), "投资人");
  assert.equal(translate("zh-CN", "Evidence check"), "证据校准");
  assert.equal(translateStatus("zh-CN", "user-evidence"), "目标用户报告");
  assert.equal(translateStatus("zh-CN", "product-engineering"), "产品经理报告");
  assert.equal(translateStatus("zh-CN", "business-investment"), "投资人报告");
  assert.equal(translateStatus("zh-CN", "evidence-auditor"), "证据校准报告");
});

test("global bilingual typography keeps short actions intact and uses phrase-aware Chinese wrapping", () => {
  const css = readFileSync(join(sourceDirectory, "app/(workspace)/globals.css"), "utf8");
  assert.match(css, /word-break:\s*auto-phrase/u);
  assert.match(css, /word-break:\s*keep-all/u);
  assert.match(css, /white-space:\s*nowrap/u);
  assert.match(css, /html\[lang="zh-CN"\]/u);
  assert.match(css, /html\[lang="en"\]/u);
});

test("durable workflow statuses are localized without changing their source values", () => {
  assert.equal(translateStatus("zh-CN", "PLANNED"), "已规划");
  assert.equal(translateStatus("zh-CN", "STANDARD_DRIFT"), "标准漂移");
  assert.equal(translateStatus("en", "READ_ONLY"), "READ ONLY");
});

test("fixed intake questions are localized by stable field without changing API data", () => {
  assert.equal(translateGapQuestion("zh-CN", "target_user", "Who is the primary target user?"), "产品的主要目标用户是谁？");
  assert.equal(translateGapQuestion("zh-CN", "payer", "Who pays for the product or service?"), "谁会为这个产品或服务付费？");
  assert.equal(translateGapQuestion("zh-CN", "region", "Which region is this validation for?"), "这次预测主要面向哪个地区？");
  assert.equal(translateGapQuestion("zh-CN", "validation_goal", "What decision should this validation help you make?"), "你希望这次预测帮助做出什么决策？");
  assert.equal(translateGapQuestion("en", "stage", "What is the current product stage?"), "What is the current product stage?");
  assert.equal(translateGapQuestion("zh-CN", "custom", "自定义问题"), "自定义问题");
});

test("every static translation call has a complete Chinese translation", () => {
  const missing: string[] = [];
  const placeholderDrift: string[] = [];
  for (const file of sourceFiles(sourceDirectory).filter(path => [".ts", ".tsx"].includes(extname(path)))) {
    const source = withoutComments(readFileSync(file, "utf8"));
    for (const match of source.matchAll(/\bt\(\s*(["'])(.*?)\1/gu)) {
      const key = match[2];
      if (!hasTranslation("zh-CN", key)) missing.push(`${relative(sourceDirectory, file)}: ${key}`);
      const sourcePlaceholders = [...key.matchAll(/\{(\w+)\}/gu)].map(value => value[1]).sort();
      const translatedPlaceholders = [...translate("zh-CN", key).matchAll(/\{(\w+)\}/gu)].map(value => value[1]).sort();
      if (sourcePlaceholders.join("|") !== translatedPlaceholders.join("|")) placeholderDrift.push(key);
    }
  }
  assert.deepEqual(missing, []);
  assert.deepEqual(placeholderDrift, []);
});

test("all data-driven visible labels have English translations", () => {
  const intake = INTAKE_SECTIONS.flatMap(section => [
    section.title,
    section.subtitle,
    ...section.fields.flatMap(field => [field.label, field.hint, field.placeholder]),
  ]);
  const dynamic = [
    ...intake,
    ...Object.values(SOURCE_LABELS),
    ...Object.values(VOICE_STATUS_TEXT).filter(Boolean),
    ...FACT_SECTORS.map(value => value.name),
    ...JUDGMENT_DIMENSIONS.map(value => value.name),
    ...SUPERVISOR_STAGES.map(value => value.label),
    ...["COMPLETED", "RUNNING", "WAITING_FOR_USER", "PLANNED", "FAILED", "DRAFT"].map(beadSignal),
  ];
  assert.deepEqual(dynamic.filter(key => !hasTranslation("en", key)), []);
});

test("client surfaces contain no untranslated Chinese copy", () => {
  const allowed = new Set([
    join(sourceDirectory, "components/i18n/LocaleProvider.tsx"),
  ]);
  const violations: string[] = [];
  for (const file of sourceFiles(sourceDirectory).filter(path => extname(path) === ".tsx" && !path.endsWith("layout.tsx") && !allowed.has(path))) {
    const source = withoutComments(readFileSync(file, "utf8"));
    if (/[\u3400-\u9fff]/u.test(source)) violations.push(relative(sourceDirectory, file));
  }
  assert.deepEqual(violations, []);
});
