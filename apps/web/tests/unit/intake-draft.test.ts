import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ALL_INTAKE_FIELDS,
  completionOf,
  describeMaterial,
  fieldSourceOf,
  INTAKE_SECTIONS,
  mergeExtraction,
  REQUIRED_FIELDS,
} from "../../src/lib/intake-draft.ts";

test("both intake modes share the same section/field contract", () => {
  const keys = INTAKE_SECTIONS.flatMap(section => section.fields.map(field => field.key));
  assert.deepEqual(ALL_INTAKE_FIELDS.map(field => field.key), keys);
  for (const required of REQUIRED_FIELDS) assert.ok(keys.includes(required), `missing ${required}`);
  assert.ok(ALL_INTAKE_FIELDS.some(field => field.key === "team"));
  assert.ok(ALL_INTAKE_FIELDS.some(field => field.key === "timing"));
  assert.equal(new Set(keys).size, keys.length);
});

test("manual input always beats a model draft on merge", () => {
  const merged = mergeExtraction(
    { problem: "用户自己写的痛点" },
    { problem: "模型猜的痛点", payer: "模型草稿付费者" },
  );
  assert.equal(merged.fields.problem, "用户自己写的痛点");
  assert.equal(merged.sources.problem, "user");
  assert.equal(merged.fields.payer, "模型草稿付费者");
  assert.equal(merged.sources.payer, "model");
});

test("empty extraction values never overwrite or create fields", () => {
  const merged = mergeExtraction({}, { problem: "  ", payer: null as unknown as string });
  assert.equal(merged.fields.problem, undefined);
  assert.equal(merged.fields.payer, undefined);
});

test("field sources report user / model / missing / unknown truthfully", () => {
  const fields = { problem: "真实", payer: "unknown", region: "" };
  const sources = { problem: "user" as const, payer: "user" as const };
  assert.equal(fieldSourceOf("problem", fields, sources), "user");
  assert.equal(fieldSourceOf("payer", fields, sources), "unknown");
  assert.equal(fieldSourceOf("region", fields, sources), "missing");
  assert.equal(fieldSourceOf("stage", fields, sources), "missing");
});

test("opaque documents are never claimed as parsed", () => {
  const docx = describeMaterial("plan.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
  assert.equal(docx.kind, "opaque");
  assert.match(docx.status, /私有材料/);
  assert.ok(!/已自动读懂|已读取/.test(docx.status));
});

test("a parsed PDF reports its real extracted character count", () => {
  const pdf = describeMaterial("deck.pdf", "application/pdf", 12345);
  assert.equal(pdf.kind, "pdf");
  assert.equal(pdf.ok, true);
  assert.match(pdf.status, /12,345/);
  const pending = describeMaterial("deck.pdf", "application/pdf");
  assert.equal(pending.ok, false);
});

test("completion counts only trimmed required fields", () => {
  const { filled, total, percent } = completionOf({ problem: "x", core_features: "  ", payer: "y" });
  assert.equal(total, REQUIRED_FIELDS.length);
  assert.equal(filled, 2);
  assert.equal(percent, Math.round((2 / REQUIRED_FIELDS.length) * 100));
});
