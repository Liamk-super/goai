import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

const sources = Object.freeze({
  user: new URL("./user-kb.v1.md", import.meta.url),
  evidence_auditor: new URL("./evidence-auditor-kb.v1.md", import.meta.url),
});

function sha256(value) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function parseSections(markdown, source) {
  const matches = [...markdown.matchAll(/^###\s+(KB-(?:USR|EVD)-[^\s]+)(?:\s+.*)?$/gm)];
  const entries = [];
  for (let index = 0; index < matches.length; index += 1) {
    const match = matches[index];
    const end = matches[index + 1]?.index ?? markdown.length;
    const content = markdown.slice(match.index, end).trim();
    entries.push({
      knowledge_id: match[1],
      source,
      content,
      content_sha256: sha256(content),
    });
  }
  return entries;
}

let cached;

export async function loadKnowledgeIndex() {
  if (cached) return cached;
  const records = (
    await Promise.all(
      Object.entries(sources).map(async ([source, url]) =>
        parseSections(await readFile(url, "utf8"), source),
      ),
    )
  ).flat();
  const byId = new Map();
  for (const record of records) {
    if (byId.has(record.knowledge_id)) throw new Error(`duplicate knowledge id ${record.knowledge_id}`);
    byId.set(record.knowledge_id, Object.freeze(record));
  }
  cached = Object.freeze({
    version: "1.0",
    package_sha256: sha256(
      JSON.stringify(
        [...byId.values()].map(({ knowledge_id, content_sha256 }) => ({ knowledge_id, content_sha256 })),
      ),
    ),
    records: byId,
  });
  return cached;
}

export async function getKnowledge(knowledgeIds) {
  const index = await loadKnowledgeIndex();
  return knowledgeIds.map((knowledgeId) => {
    const found = index.records.get(knowledgeId);
    if (!found) throw new Error(`knowledge id is not registered: ${knowledgeId}`);
    return found;
  });
}
