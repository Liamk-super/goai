import assert from "node:assert/strict";
import test from "node:test";

import {
  buildModelCorpus,
  detectTableFromRows,
  fitModelContent,
  hasStructuredVisualCue,
  redactSensitiveText,
  type PdfPageAnalysis,
} from "../../src/lib/pdf-text.ts";

test("fitModelContent preserves short material", () => {
  assert.deepEqual(fitModelContent("  first line  \nsecond line\0"), {
    text: "first line\nsecond line",
    truncated: false,
  });
});

test("fitModelContent preserves both ends when model input must be truncated", () => {
  const fitted = fitModelContent(`BEGIN-${"x".repeat(100)}-END`, 80);
  assert.equal(fitted.truncated, true);
  assert.match(fitted.text, /^BEGIN-/);
  assert.match(fitted.text, /-END$/);
  assert.ok(fitted.text.length <= 80);
});

function page(pageNumber: number, text: string, table = detectTableFromRows([])): PdfPageAnalysis {
  return {
    pageNumber,
    text,
    characterCount: text.length,
    textStatus: text.length < 120 ? "LOW_DENSITY" : "READ",
    table,
    visual: {
      status: "NOT_DETECTED",
      recognitionType: "TEXT",
      imageCount: 0,
      rotationDegrees: 0,
      summary: null,
      confidence: 1,
      source: "LOCAL_PDFJS",
    },
  };
}

test("table detection preserves headers, rows, percentages, and confidence", () => {
  const table = detectTableFromRows([
    ["指标", "数量", "占比"],
    ["需要商品图", "359", "71.3%"],
    ["需要短视频", "331", "65.8%"],
    ["其他", "12", "2.4%"],
  ]);
  assert.equal(table.status, "DETECTED");
  assert.deepEqual(table.headers, ["指标", "数量", "占比"]);
  assert.equal(table.rows[0][2], "71.3%");
  assert.ok(table.confidence >= 0.6);
});

test("structured visual cues include vector diagrams and two-column survey tables", () => {
  assert.equal(hasStructuredVisualCue("图 1 ： ‘创易’平台三层能力架构图"), true);
  assert.equal(hasStructuredVisualCue("调研维度 核心发现\n71.3% 无专职人员\n表 3 ：市场调研核心发现汇总"), true);
  assert.equal(hasStructuredVisualCue("普通正文没有图表标题"), false);
});

test("two-column survey tables preserve the decision-relevant percentages", () => {
  const table = detectTableFromRows([
    ["调研维度", "核心发现"],
    ["国际化营销能力缺口", "71.3% 无专职海外营销人员，65.8% 从未制作多语言营销视频"],
    ["多模态本地化需求", "82.6% 需组合平均 2.7 个工具，73.4% 出现术语不一致"],
    ["文化适配困境", "58.3% 曾遭受损失，91.7% 希望自动适配文化偏好"],
    ["跨平台运营痛感", "68.4% 同时运营多个平台，81.7% 希望自动适配"],
  ]);
  assert.equal(table.status, "DETECTED");
  assert.deepEqual(table.headers, ["调研维度", "核心发现"]);
  assert.match(table.rows[0][1], /71\.3%.*65\.8%/);
});

test("model corpus gives every document head, middle, and tail coverage before fillers", () => {
  const documents = ["申报书.pdf", "公司化报告.pdf", "创业计划书.pdf"].map(fileName => ({
    fileName,
    pageCount: 9,
    pages: Array.from({ length: 9 }, (_, index) => page(index + 1, `${fileName}-P${index + 1}-${"证据".repeat(30)}`)),
  }));
  const corpus = buildModelCorpus("产品描述", documents, 20_000);
  for (const document of documents) {
    assert.ok(corpus.coverage[document.fileName].includes(1));
    assert.ok(corpus.coverage[document.fileName].includes(5));
    assert.ok(corpus.coverage[document.fileName].includes(9));
    assert.match(corpus.text, new RegExp(`${document.fileName} / 第 5 页`));
  }
});

test("table and visual pages are prioritized in bounded corpus", () => {
  const detected = detectTableFromRows([
    ["阶段", "收入", "月份"],
    ["第一阶段", "10", "1-6"],
    ["第二阶段", "20", "7-12"],
  ]);
  const pages = Array.from({ length: 80 }, (_, index) => page(index + 1, `page-${index + 1}-${"x".repeat(200)}`));
  pages[74] = page(75, "三阶段收入预测表", detected);
  const corpus = buildModelCorpus("", [{ fileName: "计划书.pdf", pageCount: 80, pages }], 5_000);
  assert.ok(corpus.coverage["计划书.pdf"].includes(75));
  assert.match(corpus.text, /三阶段收入预测表/);
});

test("model context redacts contact and licence identifiers without losing page evidence", () => {
  const raw = "联系电话 18320796959 统一社会信用代码 91441900MAK7TH1G12 email owner@example.com";
  const redacted = redactSensitiveText(raw);
  assert.doesNotMatch(redacted, /18320796959|91441900MAK7TH1G12|owner@example\.com/);
  assert.match(redacted, /联系电话/);
  const corpus = buildModelCorpus("", [{ fileName: "报告.pdf", pageCount: 1, pages: [page(1, raw)] }]);
  assert.match(corpus.text, /报告\.pdf \/ 第 1 页/);
  assert.doesNotMatch(corpus.text, /18320796959|91441900MAK7TH1G12/);
});
