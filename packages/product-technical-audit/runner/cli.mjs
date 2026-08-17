import process from "node:process";
import { buildProductTechnicalReport } from "../src/index.mjs";

let raw = "";
for await (const chunk of process.stdin) raw += chunk;
try {
  process.stdout.write(`${JSON.stringify(buildProductTechnicalReport(JSON.parse(raw)))}\n`);
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
}
