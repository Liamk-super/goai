import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { runAudit } from "../src/index.mjs";

const skillDir = resolve(fileURLToPath(new URL("..", import.meta.url)));
const repoRoot = resolve(skillDir, "..", "..");
const outputDir = resolve(repoRoot, "deliverables", "evidence-calibration-v1");
const input = JSON.parse(readFileSync(resolve(skillDir, "examples", "demo.input.json"), "utf8"));
const result = runAudit(input);
if (result.status !== "completed") throw new Error(result.result_summary);
mkdirSync(outputDir, { recursive: true });
writeFileSync(resolve(outputDir, "evidence_calibration_result.json"), `${JSON.stringify(result, null, 2)}\n`);
writeFileSync(resolve(outputDir, "evidence_calibration_summary.html"), result.structured_output.reports.summary_html);
writeFileSync(resolve(outputDir, "evidence_calibration_full.html"), result.structured_output.reports.full_html);
console.log(JSON.stringify({ outputDir, claims: result.structured_output.calibration_decisions.length, digest: result.structured_output.structured_output_digest }, null, 2));
