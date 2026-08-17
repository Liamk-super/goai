import assert from "node:assert/strict";
import { test } from "node:test";
import { productNameLength } from "../../src/lib/product-name.ts";

test("long Chinese and mixed product names use the clamped wheel treatment", () => {
  assert.equal(productNameLength("校园助手"), "short");
  assert.equal(productNameLength("这是一个用于学生创新创业的智能产品验证平台"), "medium");
  assert.equal(productNameLength("AI Student Innovation Product Management & Commercial Validation Platform 2026"), "long");
  assert.equal(productNameLength("这是一款面向高校学生创新创业团队提供产品分析用户研究商业验证证据校准风险识别行动建议版本对比历史追踪协作管理的一站式智能爆款预测平台"), "long");
});
