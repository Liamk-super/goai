import process from "node:process";

import { describeProtocol, resume, RunnerProtocolError, start, submit } from "./protocol.mjs";

const MAX_STDIN_BYTES = 2_000_000;

async function readRequest() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_STDIN_BYTES) throw new RunnerProtocolError("REQUEST_TOO_LARGE", "runner request exceeds two megabytes");
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new RunnerProtocolError("REQUEST_INVALID", "runner stdin must contain one JSON object");
  }
}

async function dispatch(request) {
  if (request.action === "describe") return { status: "ok", ...(await describeProtocol()) };
  if (request.action === "start") return start(request.input, { now: request.now });
  if (request.action === "resume") {
    return resume(request.checkpoint, request.expected_revision, request.checkpoint_hash);
  }
  if (request.action === "submit") return submit(request);
  throw new RunnerProtocolError("ACTION_INVALID", "action must be describe, start, resume, or submit");
}

try {
  const response = await dispatch(await readRequest());
  process.stdout.write(`${JSON.stringify(response)}\n`);
} catch (error) {
  if (error instanceof RunnerProtocolError) {
    process.stdout.write(
      `${JSON.stringify({ status: "error", error_code: error.code, message: error.message, details: error.details })}\n`,
    );
  } else {
    process.stderr.write("user-validation runner failed unexpectedly\n");
    process.exitCode = 1;
  }
}
