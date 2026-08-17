import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const layoutSource = readFileSync(
  new URL("../../src/app/(workspace)/projects/[projectId]/new-evaluation/layout.tsx", import.meta.url),
  "utf8",
);
const landingSource = readFileSync(
  new URL("../../src/components/landing/PublicWheelLanding.tsx", import.meta.url),
  "utf8",
);

test("recorded mode cannot enter or create an executable evaluation", () => {
  assert.match(layoutSource, /executionMode\(\) === "RECORDED"/u);
  assert.match(layoutSource, /redirect\("\/recorded-snapshot"\)/u);
  assert.match(landingSource, /if \(recordedMode\) \{[\s\S]*?window\.location\.assign\("\/recorded-snapshot\?intake=1"\)/u);
  assert.match(landingSource, /useState\(startOpen\)/u);
  assert.ok(landingSource.indexOf("if (recordedMode)") < landingSource.indexOf("createProject(session.workspaceId"));
});
