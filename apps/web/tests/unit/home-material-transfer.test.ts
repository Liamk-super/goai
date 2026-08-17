import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { mergePendingIntakeFiles } from "../../src/lib/pending-intake-files.ts";

const landingSource = readFileSync(
  new URL("../../src/components/landing/PublicWheelLanding.tsx", import.meta.url),
  "utf8",
);
const evaluationSource = readFileSync(
  new URL("../../src/app/(workspace)/projects/[projectId]/new-evaluation/page.tsx", import.meta.url),
  "utf8",
);

test("home intake accepts multiple files through selection and drag-and-drop", () => {
  assert.match(landingSource, /type="file"[\s\S]*?multiple/u);
  assert.match(landingSource, /onDrop=/u);
  assert.match(landingSource, /event\.dataTransfer\.files/u);
  assert.doesNotMatch(landingSource, /setMaterialName/u);
});

test("home materials cross the root-layout navigation without asking for them again", () => {
  assert.match(landingSource, /stagePendingIntakeFiles/u);
  assert.match(evaluationSource, /takePendingIntakeFiles/u);
  assert.doesNotMatch(landingSource, /The selected file will be requested again after the project is created\./u);
});

test("repeated selection appends unique files without dropping the existing list", () => {
  const first = new File(["one"], "research.pdf", { type: "application/pdf", lastModified: 1 });
  const duplicate = new File(["one"], "research.pdf", { type: "application/pdf", lastModified: 1 });
  const second = new File(["two"], "interviews.txt", { type: "text/plain", lastModified: 2 });

  assert.deepEqual(mergePendingIntakeFiles([first], [duplicate, second]), [first, second]);
});
