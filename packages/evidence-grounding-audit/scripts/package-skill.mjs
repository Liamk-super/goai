import { mkdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const skillDir = resolve(fileURLToPath(new URL("..", import.meta.url)));
const repoRoot = resolve(skillDir, "..", "..");
const outDir = resolve(repoRoot, "deliverables", "evidence-calibration-v1");
const output = resolve(outDir, "evidence-grounding-audit-v1.zip");
mkdirSync(outDir, { recursive: true });
const result = spawnSync("tar", ["-a", "-cf", output, "--exclude=node_modules", "packages/evidence-grounding-audit"], { cwd: repoRoot, encoding: "utf8" });
if (result.status !== 0) throw new Error(result.stderr || result.stdout || `tar exited ${result.status}`);
console.log(JSON.stringify({ output }, null, 2));
