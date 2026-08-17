import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ensureEvaluationVersion,
  evaluationPlanIdempotencyKey,
  evaluationValidationScriptIdempotencyKey,
  existingRunReturnPath,
  evaluationDraftKey,
  evaluationVersionUrl,
  evaluationVersionState,
  loadEvaluationDraft,
  resumableEvaluationVersionId,
  saveEvaluationDraft,
  shouldReuseValidationDraft,
  type EvaluationDraftSession,
} from "../../src/lib/evaluation-draft-session.ts";

function draft(projectId: string, product: string): EvaluationDraftSession {
  return {
    projectId,
    mode: "structured",
    phase: "review",
    activeSector: 2,
    fields: { one_line_value_claim: product },
    sources: { one_line_value_claim: "user" },
    rawContent: product,
    externalConsent: false,
    questions: [],
    correlationId: "",
    versionId: "",
    versionLabel: "V2",
    validationTasks: [],
    validationDirty: false,
    evidenceKind: "interview",
    evidenceTier: "E3",
    evidenceSource: "",
    evidenceObservedAt: "",
    evidenceSampleSize: "",
    evidenceSegment: "",
    evidenceObservation: "",
    savedAt: "2026-08-13T00:00:00Z",
  };
}

test("drafts are isolated by project id and survive repeated navigation", () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
  };
  saveEvaluationDraft(storage, draft("project-a", "CreaTrades"));
  saveEvaluationDraft(storage, draft("project-b", "另一项目"));

  for (let index = 0; index < 3; index += 1) {
    const restored = loadEvaluationDraft(storage, "project-a");
    assert.equal(restored?.fields.one_line_value_claim, "CreaTrades");
    saveEvaluationDraft(storage, { ...restored!, rawContent: `CreaTrades ${index}` });
  }
  assert.equal(loadEvaluationDraft(storage, "project-b")?.rawContent, "另一项目");
  assert.notEqual(evaluationDraftKey("project-a"), evaluationDraftKey("project-b"));
});

test("only a durable product version id can resume browser material collection", () => {
  assert.equal(
    resumableEvaluationVersionId("4ba2e576-d45b-4eb5-9344-f3686e830e46"),
    "4ba2e576-d45b-4eb5-9344-f3686e830e46",
  );
  assert.equal(resumableEvaluationVersionId("../another-version"), null);
  assert.equal(resumableEvaluationVersionId(null), null);
});

test("an existing run return target never creates another evaluation", () => {
  assert.equal(
    existingRunReturnPath("3ff153ee-5064-4dee-8898-2fe3f0498c2c"),
    "/runs/3ff153ee-5064-4dee-8898-2fe3f0498c2c",
  );
  assert.equal(existingRunReturnPath("../runs/new"), null);
});

test("a confirmed rerun gets a new stable plan idempotency boundary", () => {
  const versionId = "4ba2e576-d45b-4eb5-9344-f3686e830e46";
  const previousRunId = "3ff153ee-5064-4dee-8898-2fe3f0498c2c";
  assert.equal(evaluationPlanIdempotencyKey(versionId, null), `final-review:${versionId}:plan`);
  assert.equal(
    evaluationPlanIdempotencyKey(versionId, previousRunId),
    `final-review:${versionId}:after:${previousRunId}:plan`,
  );
  assert.equal(evaluationPlanIdempotencyKey(versionId, "../invalid"), `final-review:${versionId}:plan`);
  assert.equal(evaluationValidationScriptIdempotencyKey(versionId, null), `final-review:${versionId}:script`);
  assert.equal(
    evaluationValidationScriptIdempotencyKey(versionId, previousRunId),
    `final-review:${versionId}:after:${previousRunId}:script`,
  );
  assert.equal(
    evaluationValidationScriptIdempotencyKey(versionId, "../invalid"),
    `final-review:${versionId}:script`,
  );
});

test("a rerun reuses one to five existing validation tasks instead of regenerating them", () => {
  const previousRunId = "3ff153ee-5064-4dee-8898-2fe3f0498c2c";
  const task = {
    task_key: "complete_core_flow",
    description: "Complete the product core flow",
    expected_observable_outcome: "A durable result is visible",
    max_steps: 20,
    rationale: "Previously confirmed",
    source_hints: [],
  };

  assert.equal(shouldReuseValidationDraft(previousRunId, [task]), true);
  assert.equal(shouldReuseValidationDraft(previousRunId, Array.from({ length: 5 }, () => task)), true);
  assert.equal(shouldReuseValidationDraft(previousRunId, []), false);
  assert.equal(shouldReuseValidationDraft(previousRunId, Array.from({ length: 6 }, () => task)), false);
  assert.equal(shouldReuseValidationDraft(null, [task]), false);
});

test("one intake keeps the first created product version across stale and concurrent callers", async () => {
  const state = evaluationVersionState();
  let creations = 0;
  const create = async () => {
    creations += 1;
    await Promise.resolve();
    return "4ba2e576-d45b-4eb5-9344-f3686e830e46";
  };

  const [uploadVersion, reviewVersion] = await Promise.all([
    ensureEvaluationVersion(state, create),
    ensureEvaluationVersion(state, create),
  ]);
  const confirmationVersion = await ensureEvaluationVersion(state, create);

  assert.equal(uploadVersion, reviewVersion);
  assert.equal(reviewVersion, confirmationVersion);
  assert.equal(creations, 1);
});

test("the active product version is pinned in the browser URL without dropping return context", () => {
  assert.equal(
    evaluationVersionUrl(
      "http://127.0.0.1:3000/projects/project-a/new-evaluation?returnRunId=run-a#materials",
      "4ba2e576-d45b-4eb5-9344-f3686e830e46",
    ),
    "http://127.0.0.1:3000/projects/project-a/new-evaluation?returnRunId=run-a&versionId=4ba2e576-d45b-4eb5-9344-f3686e830e46#materials",
  );
});
