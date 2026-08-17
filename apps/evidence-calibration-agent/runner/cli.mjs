import process from "node:process";
import { runAgent } from "../src/index.mjs";

let body = "";
process.stdin.setEncoding("utf8");
for await (const chunk of process.stdin) body += chunk;
const output = runAgent(JSON.parse(body));
process.stdout.write(`${JSON.stringify(output)}\n`);
process.exitCode = output.status === "SUCCEEDED" ? 0 : 2;
