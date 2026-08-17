/**
 * Regenerates the runnable example outputs from REAL pipeline runs.
 *
 * Every *.output.example.json in examples/ is produced by this script; none is
 * hand-authored. Re-run after any behavioural change:
 *   node skills/user-validation-designer/scripts/generate-examples.mjs
 */
import { writeFileSync } from "node:fs";
import {
  renderSummaryReport,
  renderSummaryReportHtml,
  renderFullReport,
  renderFullReportHtml,
} from "../src/presentation.mjs";
import { OFFERPILOT_GOLDEN } from "../tests/fixtures/offerpilot-presentation.mjs";
import { loadExample, runBound, runUnbound } from "../tests/helpers/run.mjs";

const OUT = new URL("../examples/", import.meta.url);

function stableEnvelope(result) {
  // task_id / timestamps are runtime-dependent; pin them so the committed
  // examples diff only on real behavioural change.
  const clone = JSON.parse(JSON.stringify(result));
  if (clone.meta) {
    clone.meta.generated_at = "2026-08-09T02:00:00.000Z";
    if (clone.meta.duration_ms !== undefined) clone.meta.duration_ms = 0;
  }
  if (Array.isArray(clone.structured_output?.execution_log)) {
    for (const entry of clone.structured_output.execution_log) {
      if (entry.at !== undefined) entry.at = "2026-08-09T02:00:00.000Z";
      if (entry.duration_ms !== undefined) entry.duration_ms = 0;
    }
  }
  return clone;
}

function write(name, payload) {
  writeFileSync(new URL(name, OUT), `${JSON.stringify(stableEnvelope(payload), null, 2)}\n`, "utf8");
  const so = payload.structured_output;
  console.log(
    `${name.padEnd(42)} status=${payload.status}` +
      (payload.failure_reason ? ` failure=${payload.failure_reason}` : "") +
      (so ? ` uvj=${so.user_value_judgment} oj=${so.overall_judgment}` : ""),
  );
}

const cases = [
  ["output.example.json", () => runBound(loadExample("input.example.json"))],
  ["broad-target-user.output.example.json", () => runBound(loadExample("broad-target-user.example.json"))],
  ["no-product-task.output.example.json", () => runBound(loadExample("no-product-task.example.json"))],
  ["simulation-only.output.example.json", () => runBound(loadExample("simulation-only.example.json"))],
  ["with-real-evidence.output.example.json", () => runBound(loadExample("with-real-evidence.example.json"))],
  ["regression.output.example.json", () => runBound(loadExample("regression.example.json"))],
  ["tool-unavailable.output.example.json", () => runUnbound(loadExample("input.example.json"))],
];

let failed = 0;
for (const [name, run] of cases) {
  try {
    write(name, await run());
  } catch (error) {
    failed += 1;
    console.error(`${name.padEnd(42)} THREW ${error.message}`);
  }
}

const offerPilotSummary = renderSummaryReport(OFFERPILOT_GOLDEN);
const offerPilotSummaryHtml = renderSummaryReportHtml(OFFERPILOT_GOLDEN);
const offerPilotFull = renderFullReport(OFFERPILOT_GOLDEN);
const offerPilotFullHtml = renderFullReportHtml(OFFERPILOT_GOLDEN);
writeFileSync(new URL("offerpilot-summary-report.example.md", OUT), `${offerPilotSummary}\n`, "utf8");
writeFileSync(new URL("offerpilot-summary-report.example.html", OUT), offerPilotSummaryHtml, "utf8");
writeFileSync(new URL("offerpilot-full-report.example.md", OUT), `${offerPilotFull}\n`, "utf8");
writeFileSync(new URL("offerpilot-full-report.example.html", OUT), offerPilotFullHtml, "utf8");
// Keep the historical filenames as aliases of the concise report.
writeFileSync(new URL("offerpilot-human-report.example.md", OUT), `${offerPilotSummary}\n`, "utf8");
writeFileSync(new URL("offerpilot-human-report.example.html", OUT), offerPilotSummaryHtml, "utf8");
console.log(`${"offerpilot-summary-report.example.md".padEnd(42)} chars=${offerPilotSummary.length}`);
console.log(`${"offerpilot-summary-report.example.html".padEnd(42)} chars=${offerPilotSummaryHtml.length}`);
console.log(`${"offerpilot-full-report.example.md".padEnd(42)} chars=${offerPilotFull.length}`);
console.log(`${"offerpilot-full-report.example.html".padEnd(42)} chars=${offerPilotFullHtml.length}`);

if (failed > 0) process.exitCode = 1;

