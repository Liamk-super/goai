import assert from "node:assert/strict";
import test from "node:test";

import { createReferenceExecutor, REFERENCE_TIMESTAMP } from "./fixtures/reference-executor.mjs";
import { loadExample } from "./helpers/run.mjs";
import { resume, RunnerProtocolError, start, submit } from "../runner/protocol.mjs";

test("runner: resumes one immutable Agent step at a time and matches the frozen engine", async () => {
  const input = loadExample("input.example.json");
  const executor = createReferenceExecutor();
  let response = await start(input, { now: REFERENCE_TIMESTAMP });
  let transitions = 0;

  while (response.status === "awaiting_step") {
    const outcome = await executor(
      { id: response.step.step_id },
      input,
      { ...response.step.accumulated_context, attempt: response.step.attempt },
    );
    response = await submit({
      checkpoint: response.checkpoint,
      expected_revision: response.revision,
      checkpoint_hash: response.checkpoint_hash,
      step_id: response.step.step_id,
      attempt: response.step.attempt,
      output: outcome,
    });
    transitions += 1;
    assert.ok(transitions < 20, "runner must terminate inside the bounded transition budget");
  }

  assert.equal(response.status, "completed");
  assert.equal(response.result.status, "completed");
  assert.match(response.result_sha256, /^[a-f0-9]{64}$/);
  assert.equal(response.revision, transitions);
});

test("runner: resume is idempotent for an unchanged checkpoint", async () => {
  const first = await start(loadExample("input.example.json"), { now: REFERENCE_TIMESTAMP });
  const replay = await resume(first.checkpoint, first.revision, first.checkpoint_hash);
  assert.equal(replay.status, "awaiting_step");
  assert.equal(replay.step.step_id, first.step.step_id);
  assert.equal(replay.checkpoint_hash, first.checkpoint_hash);
});

test("runner: rejects a tampered checkpoint before executing a step", async () => {
  const first = await start(loadExample("input.example.json"), { now: REFERENCE_TIMESTAMP });
  const tampered = structuredClone(first.checkpoint);
  tampered.input.product_profile.name = "tampered";
  await assert.rejects(
    () => resume(tampered, first.revision, first.checkpoint_hash),
    (error) => error instanceof RunnerProtocolError && error.code === "CHECKPOINT_HASH_MISMATCH",
  );
});

test("runner: invalid Agent output does not advance the revision", async () => {
  const first = await start(loadExample("input.example.json"), { now: REFERENCE_TIMESTAMP });
  await assert.rejects(
    () =>
      submit({
        checkpoint: first.checkpoint,
        expected_revision: first.revision,
        checkpoint_hash: first.checkpoint_hash,
        step_id: first.step.step_id,
        attempt: first.step.attempt,
        output: { status: "completed" },
      }),
    (error) => error instanceof RunnerProtocolError && error.code === "STEP_OUTPUT_INVALID",
  );
  const replay = await resume(first.checkpoint, first.revision, first.checkpoint_hash);
  assert.equal(replay.revision, first.revision);
});
