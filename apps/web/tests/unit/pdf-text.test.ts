import assert from "node:assert/strict";
import test from "node:test";

import { fitModelContent } from "../../src/lib/pdf-text.ts";

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
