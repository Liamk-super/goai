import { readFileSync } from "node:fs";

export function normalizeZipEntry(entry) {
  return String(entry).replaceAll("\\", "/").replace(/^\/+/, "");
}

export function assertPosixEntryNames(entries) {
  const invalid = entries.filter((entry) => entry.includes("\\") || entry.startsWith("/") || /^[A-Za-z]:/.test(entry));
  if (invalid.length > 0) throw new Error(`non-POSIX or absolute ZIP entries: ${invalid.join(", ")}`);
  return true;
}

export function readZipEntryNames(zipPath) {
  const buffer = readFileSync(zipPath);
  const eocdSignature = 0x06054b50;
  let eocd = -1;
  for (let offset = buffer.length - 22; offset >= Math.max(0, buffer.length - 65_557); offset -= 1) {
    if (buffer.readUInt32LE(offset) === eocdSignature) {
      eocd = offset;
      break;
    }
  }
  if (eocd < 0) throw new Error("ZIP end-of-central-directory record not found");

  const entryCount = buffer.readUInt16LE(eocd + 10);
  let offset = buffer.readUInt32LE(eocd + 16);
  const entries = [];
  for (let index = 0; index < entryCount; index += 1) {
    if (buffer.readUInt32LE(offset) !== 0x02014b50) throw new Error(`invalid central directory entry ${index}`);
    const nameLength = buffer.readUInt16LE(offset + 28);
    const extraLength = buffer.readUInt16LE(offset + 30);
    const commentLength = buffer.readUInt16LE(offset + 32);
    entries.push(buffer.subarray(offset + 46, offset + 46 + nameLength).toString("utf8"));
    offset += 46 + nameLength + extraLength + commentLength;
  }
  return entries;
}

export function verifyStandaloneZip(zipPath) {
  const entries = readZipEntryNames(zipPath);
  assertPosixEntryNames(entries);
  const required = [
    "PACKAGE_MANIFEST.md",
    "RESTORE.md",
    "package.json",
    "skills/_shared/schema/evidence-card.canonical.json",
    "skills/user-validation-designer/SKILL.md",
    "skills/user-validation-designer/tests/role-acceptance-test.mjs",
  ];
  for (const name of required) {
    if (!entries.includes(name)) throw new Error(`standalone ZIP missing ${name}`);
  }
  if (entries.some((entry) => entry.startsWith("skills/product-technical-audit/"))) {
    throw new Error("standalone UVD ZIP must not include product-technical-audit");
  }
  if (entries.some((entry) => entry.startsWith("skills/_shared/tests/"))) {
    throw new Error("standalone UVD ZIP must not include cross-skill tests that require PTA");
  }
  return { entries, fileCount: entries.filter((entry) => !entry.endsWith("/")).length };
}

if (process.argv[1] && new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1") === process.argv[1].replaceAll("\\", "/")) {
  const zipPath = process.argv[2];
  if (!zipPath) throw new Error("usage: node verify-standalone-package.mjs <zip-path>");
  const result = verifyStandaloneZip(zipPath);
  process.stdout.write(`POSIX ZIP entries verified: ${result.fileCount} files\n`);
}

