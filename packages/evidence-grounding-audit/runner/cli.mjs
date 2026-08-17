import process from "node:process";
import { runAudit } from "../src/index.mjs";

let body = "";
process.stdin.setEncoding("utf8");
for await (const chunk of process.stdin) body += chunk;
try {
  const result = runAudit(JSON.parse(body));
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exitCode = result.status === "completed" ? 0 : 2;
} catch (error) {
  process.stdout.write(`${JSON.stringify({ task_id: "invalid-task", status: "blocked", result_summary: error.message, structured_output: null, evidence_refs: [], confidence: 0, risks: [error.message], needs_human_review: false, failure_reason: "runtime_error", retryable: false })}\n`);
  process.exitCode = 2;
}
