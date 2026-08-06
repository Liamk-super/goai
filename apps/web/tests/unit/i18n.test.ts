import assert from "node:assert/strict";
import test from "node:test";

import { normalizeLocale, translate, translateStatus } from "../../src/lib/i18n.ts";

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

test("durable workflow statuses are localized without changing their source values", () => {
  assert.equal(translateStatus("zh-CN", "PLANNED"), "已规划");
  assert.equal(translateStatus("zh-CN", "STANDARD_DRIFT"), "标准漂移");
  assert.equal(translateStatus("en", "READ_ONLY"), "READ ONLY");
});
