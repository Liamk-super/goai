const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const SHA256 = /^[a-f0-9]{64}$/u;
const SOURCE_KINDS = new Set(["PUBLIC_URL", "SEARCH_RESULT", "INTERNAL_MATERIAL"]);
const SUPPORT_ROLES = new Set(["SUPPORT", "COUNTER", "BACKGROUND"]);

function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function dateTime(value) {
  if (typeof value !== "string" || !/T/u.test(value) || !/(?:Z|[+-]\d{2}:\d{2})$/u.test(value)) return null;
  return Number.isNaN(Date.parse(value)) ? null : value;
}

function publicUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? value : null;
  } catch {
    return null;
  }
}

export function normalizeReportSources(value) {
  if (!Array.isArray(value)) return [];
  const normalized = [];
  for (const source of value) {
    if (!isRecord(source)) continue;
    const sourceLocatorId = typeof source.source_locator_id === "string" ? source.source_locator_id : "";
    const evidenceId = typeof source.evidence_id === "string" ? source.evidence_id : "";
    const sourceKind = typeof source.source_kind === "string" ? source.source_kind : "";
    const title = typeof source.title === "string" ? source.title.trim() : "";
    const fetchedAt = dateTime(source.fetched_at);
    const independenceGroup = typeof source.independence_group === "string" ? source.independence_group.trim() : "";
    const contentSha256 = typeof source.content_sha256 === "string" ? source.content_sha256 : "";
    const canonicalUrl = publicUrl(source.canonical_url);
    if (
      !UUID.test(sourceLocatorId)
      || !UUID.test(evidenceId)
      || !SOURCE_KINDS.has(sourceKind)
      || !title
      || !fetchedAt
      || !isRecord(source.locator)
      || !independenceGroup
      || !SHA256.test(contentSha256)
      || (sourceKind !== "INTERNAL_MATERIAL" && !canonicalUrl)
    ) continue;
    const publishedAt = source.published_at === null ? null : dateTime(source.published_at);
    if (source.published_at !== undefined && source.published_at !== null && !publishedAt) continue;
    const directory = {
      source_locator_id: sourceLocatorId,
      evidence_id: evidenceId,
      source_kind: sourceKind,
      ...(canonicalUrl ? {canonical_url: canonicalUrl} : {}),
      title,
      publisher: typeof source.publisher === "string" && source.publisher.trim() ? source.publisher.trim() : null,
      published_at: publishedAt,
      fetched_at: fetchedAt,
      locator: {...source.locator},
      region: typeof source.region === "string" && source.region.trim() ? source.region.trim() : null,
      independence_group: independenceGroup,
      content_sha256: contentSha256,
    };
    normalized.push({
      directory,
      supportRole: SUPPORT_ROLES.has(source.support_role) ? source.support_role : "SUPPORT",
    });
  }
  return [...new Map(normalized.map((item) => [item.directory.source_locator_id, item])).values()];
}
