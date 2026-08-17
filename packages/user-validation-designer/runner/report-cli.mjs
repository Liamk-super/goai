import process from "node:process";

import { buildUserSpecialistReportV2 } from "../src/presentation.mjs";

let raw = "";
for await (const chunk of process.stdin) raw += chunk;
try {
  process.stdout.write(`${JSON.stringify(buildUserSpecialistReportV2(JSON.parse(raw)))}\n`);
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
}
