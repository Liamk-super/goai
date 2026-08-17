import process from "node:process";

import { createReferenceExecutor } from "../packages/user-validation-designer/tests/fixtures/reference-executor.mjs";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const execute = createReferenceExecutor();
const output = await execute(
  { id: request.step.step_id },
  request.input,
  { ...request.step.accumulated_context, attempt: request.step.attempt },
);
process.stdout.write(`${JSON.stringify(output)}\n`);
