import assert from "node:assert/strict";
import test from "node:test";

import { buildPlan } from "../src/index.mjs";
import { getKnowledge, loadKnowledgeIndex } from "../knowledge/registry.mjs";

test("knowledge registry: every runtime KB-USR id resolves exactly with a content hash", async () => {
  const ids = [...new Set(buildPlan().steps.flatMap((step) => step.kb))];
  const entries = await getKnowledge(ids);
  assert.equal(entries.length, ids.length);
  assert.deepEqual(entries.map((entry) => entry.knowledge_id), ids);
  for (const entry of entries) {
    assert.match(entry.content_sha256, /^[a-f0-9]{64}$/);
    assert.match(entry.content, new RegExp(`^### ${entry.knowledge_id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`));
  }
});

test("knowledge registry: user and evidence-auditor packages are both indexed", async () => {
  const index = await loadKnowledgeIndex();
  assert.match(index.package_sha256, /^[a-f0-9]{64}$/);
  assert.ok(index.records.has("KB-USR-R01"));
  assert.ok(index.records.has("KB-EVD-D03"));
});
