import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

import { buildPlan, runValidationDesign } from "../src/index.mjs";
import { createCapabilityContext } from "../src/tools/index.mjs";
import { validate } from "../src/validate.mjs";
import { getKnowledge, loadKnowledgeIndex } from "../knowledge/registry.mjs";

const PROTOCOL_VERSION = "1.0";
const SKILL_VERSION = "1.0.5";
const MAX_CHECKPOINT_BYTES = 2_000_000;
const stepSchema = JSON.parse(
  await readFile(new URL("./step-output.schema.json", import.meta.url), "utf8"),
);
const taskTemplate = await readFile(new URL("../prompts/task-template.md", import.meta.url), "utf8");

const headings = {
  s2: "## S2 Persona 与 JTBD 建模",
  s3: "## S3 使用场景与替代方案",
  s4a: "## S4a 模拟首体验",
  s4b: "## S4b 核心任务测试",
  s5: "## S5 用户假设与问题归纳",
  s6: "## S6 真实用户验证方案设计",
};

export class RunnerProtocolError extends Error {
  constructor(code, message, details = []) {
    super(message);
    this.name = "RunnerProtocolError";
    this.code = code;
    this.details = details;
  }
}

class StepRequired extends Error {
  constructor(step, input, context) {
    super(`step ${step.id} requires Agent output`);
    this.step = step;
    this.input = input;
    this.context = {
      collected: structuredClone(context.collected),
      evidenceCards: structuredClone(context.evidenceCards),
      attempt: context.attempt,
      availability: structuredClone(context.availability),
    };
  }
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalize(value[key])]),
    );
  }
  return value;
}

function digest(value) {
  return createHash("sha256").update(JSON.stringify(canonicalize(value)), "utf8").digest("hex");
}

function assertCheckpoint(checkpoint, expectedRevision, expectedHash) {
  if (!checkpoint || checkpoint.protocol_version !== PROTOCOL_VERSION || checkpoint.skill_version !== SKILL_VERSION) {
    throw new RunnerProtocolError("CHECKPOINT_INVALID", "checkpoint protocol or Skill version is incompatible");
  }
  if (!Number.isInteger(expectedRevision) || expectedRevision !== checkpoint.revision) {
    throw new RunnerProtocolError("REVISION_CONFLICT", "expected revision does not match the checkpoint");
  }
  const actual = checkpointHash(checkpoint);
  if (!expectedHash || expectedHash !== actual) {
    throw new RunnerProtocolError("CHECKPOINT_HASH_MISMATCH", "checkpoint hash does not match immutable state");
  }
  if (Buffer.byteLength(JSON.stringify(checkpoint), "utf8") > MAX_CHECKPOINT_BYTES) {
    throw new RunnerProtocolError("CHECKPOINT_TOO_LARGE", "checkpoint exceeds the two-megabyte protocol limit");
  }
}

function promptSection(stepId) {
  const heading = headings[stepId];
  if (!heading) return "";
  const start = taskTemplate.indexOf(heading);
  const next = taskTemplate.indexOf("\n## ", start + heading.length);
  return taskTemplate.slice(start, next < 0 ? taskTemplate.length : next).trim();
}

function publicContext(context) {
  return {
    collected: context.collected,
    evidence_cards: context.evidenceCards,
    attempt: context.attempt,
    availability: context.availability,
  };
}

async function awaitingResponse(checkpoint, pending) {
  return {
    status: "awaiting_step",
    protocol_version: PROTOCOL_VERSION,
    skill_version: SKILL_VERSION,
    revision: checkpoint.revision,
    checkpoint_hash: checkpointHash(checkpoint),
    checkpoint,
    step: {
      step_id: pending.step.id,
      name: pending.step.name,
      attempt: pending.context.attempt,
      capabilities: pending.step.capabilities,
      knowledge_ids: pending.step.kb,
      knowledge_entries: await getKnowledge(pending.step.kb),
      prompt: promptSection(pending.step.id),
      untrusted_input: pending.input,
      accumulated_context: publicContext(pending.context),
      output_schema: stepSchema,
    },
  };
}

function completedResponse(checkpoint, result) {
  return {
    status: "completed",
    protocol_version: PROTOCOL_VERSION,
    skill_version: SKILL_VERSION,
    revision: checkpoint.revision,
    checkpoint_hash: checkpointHash(checkpoint),
    checkpoint,
    result_sha256: digest(result),
    result,
  };
}

function checkpointKey(stepId, attempt) {
  return `${stepId}:${attempt}`;
}

async function advance(checkpoint) {
  const allowed = checkpoint.input.runtime?.allowed_tools ?? [
    "simulation_engine",
    "product_reader",
    "evidence_writer",
    "kb_retriever",
  ];
  const capabilityContext = createCapabilityContext(
    Object.fromEntries(allowed.map((name) => [name, { kind: "agentteams-user-agent" }])),
  );
  try {
    const result = await runValidationDesign(checkpoint.input, {
      now: checkpoint.now,
      capabilityContext,
      executeStep: async (step, input, context) => {
        const key = checkpointKey(step.id, context.attempt);
        if (Object.hasOwn(checkpoint.step_outputs, key)) return structuredClone(checkpoint.step_outputs[key]);
        throw new StepRequired(step, input, context);
      },
    });
    return completedResponse(checkpoint, result);
  } catch (error) {
    if (error instanceof StepRequired) return awaitingResponse(checkpoint, error);
    throw error;
  }
}

export function checkpointHash(checkpoint) {
  return digest(checkpoint);
}

export async function start(input, options = {}) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new RunnerProtocolError("INPUT_INVALID", "input must be a JSON object");
  }
  const now = options.now ?? new Date().toISOString();
  const checkpoint = {
    protocol_version: PROTOCOL_VERSION,
    skill_version: SKILL_VERSION,
    revision: 0,
    now,
    input: structuredClone(input),
    step_outputs: {},
  };
  return advance(checkpoint);
}

export async function resume(checkpoint, expectedRevision, expectedHash) {
  assertCheckpoint(checkpoint, expectedRevision, expectedHash);
  return advance(structuredClone(checkpoint));
}

export async function submit({ checkpoint, expected_revision, checkpoint_hash, step_id, attempt, output }) {
  assertCheckpoint(checkpoint, expected_revision, checkpoint_hash);
  const current = await advance(structuredClone(checkpoint));
  if (current.status !== "awaiting_step") {
    throw new RunnerProtocolError("EXECUTION_ALREADY_TERMINAL", "execution no longer accepts step output");
  }
  if (current.step.step_id !== step_id || current.step.attempt !== attempt) {
    throw new RunnerProtocolError("STEP_CONFLICT", "submitted step or attempt is not the current transition");
  }
  const checked = validate(output, stepSchema);
  if (!checked.valid) {
    throw new RunnerProtocolError(
      "STEP_OUTPUT_INVALID",
      "Agent output does not satisfy the bounded step schema",
      checked.errors,
    );
  }
  const next = structuredClone(checkpoint);
  next.step_outputs[checkpointKey(step_id, attempt)] = structuredClone(output);
  next.revision += 1;
  return advance(next);
}

export async function describeProtocol() {
  const knowledge = await loadKnowledgeIndex();
  return {
    protocol_version: PROTOCOL_VERSION,
    skill_version: SKILL_VERSION,
    steps: buildPlan().steps.map(({ id, name, capabilities, kb }) => ({ id, name, capabilities, knowledge_ids: kb })),
    knowledge_package_sha256: knowledge.package_sha256,
    max_checkpoint_bytes: MAX_CHECKPOINT_BYTES,
  };
}
