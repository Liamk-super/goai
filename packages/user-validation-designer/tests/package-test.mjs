import assert from "node:assert/strict";
import test from "node:test";

import {
  assertPosixEntryNames,
  normalizeZipEntry,
} from "../scripts/verify-standalone-package.mjs";

test("standalone package: entry normalization always emits POSIX separators", () => {
  assert.equal(
    normalizeZipEntry("skills\\user-validation-designer\\src\\index.mjs"),
    "skills/user-validation-designer/src/index.mjs",
  );
});

test("standalone package: verifier rejects backslashes and absolute paths", () => {
  assert.throws(() => assertPosixEntryNames(["skills\\user-validation-designer\\SKILL.md"]));
  assert.throws(() => assertPosixEntryNames(["C:/private/file.txt"]));
  assert.throws(() => assertPosixEntryNames(["/private/file.txt"]));
  assert.equal(assertPosixEntryNames(["skills/user-validation-designer/SKILL.md"]), true);
});

