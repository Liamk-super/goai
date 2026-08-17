import { createHash } from "node:crypto";

const AGENT_CODE = { product: "product-engineering", user: "user-evidence", investment: "business-investment" };

function list(value) { return Array.isArray(value) ? value : []; }
function text(value, fallback = "") { return typeof value === "string" ? value : fallback; }
function sha(value) { return createHash("sha256").update(String(value)).digest("hex"); }

function topicFor(claim) {
  const raw = `${claim.normalized_topic ?? ""} ${claim.claim_type ?? ""} ${claim.text ?? ""}`.toLowerCase();
  if (/信任|真实(性|度)|虚假|commercial content|authentic|trust/u.test(raw)) return "trust_integrity";
  if (/product.?stage|产品阶段|early_product|mature_operation/u.test(raw)) return "product_stage";
  if (/\bmau\b|月活/u.test(raw)) return "mau";
  if (/需求|demand|need/u.test(raw)) return "user_demand";
  if (/付费|payment|willingness.to.pay/u.test(raw)) return "willingness_to_pay";
  if (/投资潜力|investment potential|investability/u.test(raw)) return "investment_potential";
  if (/市场增长|market growth/u.test(raw)) return "market_growth";
  return text(claim.normalized_topic || claim.claim_type || claim.dimension, "general").toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]+/gu, "_");
}

function normalizeEvidence(card, context) {
  const applicability = card.applicability && typeof card.applicability === "object" ? card.applicability : {};
  const source = text(card.source || card.url || card.file_ref || card.tool_run_id, "untraceable");
  const excerpt = text(card.raw_excerpt || card.observation || card.summary || card.excerpt, "");
  return {
    evidence_id: text(card.evidence_id || card.id, `EV-${sha(JSON.stringify(card)).slice(0, 12)}`),
    project_id: text(card.project_id, context.project_id),
    source,
    source_type: text(card.source_type || card.evidence_type, "unknown"),
    publisher: text(card.publisher, "unknown"),
    source_tier: text(card.source_tier, source === "untraceable" ? "untraceable" : "tier_3"),
    reliability_level: text(card.reliability_level || card.evidence_level || card.trust_level, "E0"),
    published_at: card.published_at ?? card.timestamp ?? null,
    collected_at: card.collected_at ?? card.fetched_at ?? card.timestamp ?? null,
    applicability,
    geography: card.geography ?? card.region ?? applicability.geography ?? null,
    product_version: text(card.product_version || applicability.product_version, context.product_version),
    raw_excerpt: excerpt.slice(0, 2000),
    expiry: card.expiry ?? card.valid_until ?? null,
    superseded: Boolean(card.superseded),
    simulated: Boolean(card.simulated || card.simulation_note || String(card.evidence_type ?? "").includes("simulated")),
    content_hash: text(card.content_hash || card.sha256, sha(excerpt || source)),
    original_source: card.original_source ?? card.source_identity ?? null,
    independence_group: text(card.independence_group, ""),
    source_locator_ids: list(card.source_locator_ids ?? card.locator_ids).map(String),
    content_fingerprint: text(card.content_fingerprint, sha(excerpt.replace(/\s+/gu, " ").trim().toLowerCase() || source)),
    integrity_issue: Boolean(card.integrity_issue || card.tampered),
  };
}

function normalizeClaim(raw, context, index, overrides = {}) {
  const evidenceRefs = list(raw.evidence_refs ?? raw.evidence_ids ?? raw.supporting_evidence ?? raw.supporting_refs).map(String);
  const claim = {
    claim_id: text(raw.claim_id || raw.hypothesis_id || raw.id, `CL-${context.source_agent.toUpperCase()}-${String(index + 1).padStart(3, "0")}`),
    project_id: text(raw.project_id, context.project_id),
    product_version: text(raw.product_version, context.product_version),
    source_agent: AGENT_CODE[context.source_agent],
    claim_type: text(raw.claim_type || raw.dimension, "general"),
    text: text(raw.text || raw.statement || raw.observation || raw.description, "Unspecified claim"),
    normalized_topic: text(raw.normalized_topic, ""),
    fact_inference_hypothesis: text(raw.fact_inference_hypothesis || raw.fact_type || raw.epistemic_type || raw.classification, "inference").toLowerCase(),
    criticality: ["critical", "blocking", "high"].includes(String(raw.criticality || raw.decision_impact).toLowerCase()) ? "critical" : "normal",
    scope: raw.scope ?? raw.product_scope ?? null,
    geography: raw.geography ?? raw.region ?? null,
    time_scope: raw.time_scope ?? raw.time_horizon ?? raw.period ?? null,
    metric_definition: raw.metric_definition ?? raw.metric ?? null,
    population: raw.population ?? null,
    method: raw.method ?? null,
    value: raw.value ?? raw.metric_value ?? raw.position ?? null,
    evidence_refs: evidenceRefs,
    source_status: context.status,
    ...overrides,
  };
  claim.normalized_topic = topicFor(claim);
  return claim;
}

export function adaptAgentResults(input) {
  const claims = [];
  const evidence = [];
  const reports = [];
  for (const result of input.agent_results) {
    reports.push({ source_agent: AGENT_CODE[result.source_agent], status: result.status, report_ref: result.report_ref ?? null });
    if (result.status !== "COMPLETED") continue;
    const payload = result.payload?.structured_output ?? result.payload;
    const context = {
      source_agent: result.source_agent,
      status: result.status,
      project_id: result.project_id ?? input.project_id,
      product_version: result.product_version ?? input.product_version,
    };
    const cards = list(payload.evidence ?? payload.evidence_cards ?? result.payload?.evidence);
    for (const card of cards) evidence.push(normalizeEvidence(card, context));
    let sourceClaims = list(payload.claims);
    if (result.source_agent === "user" && sourceClaims.length === 0) sourceClaims = list(payload.user_hypotheses);
    sourceClaims.forEach((claim, index) => claims.push(normalizeClaim(claim, context, index)));
  }
  const evidenceById = new Map(evidence.map((item) => [item.evidence_id, item]));
  const supportLinks = claims.flatMap((claim) => claim.evidence_refs.map((evidenceId) => ({
    claim_id: claim.claim_id,
    evidence_id: evidenceId,
    relation: "support",
    strength: evidenceById.has(evidenceId) ? "candidate" : "missing",
    reasoning_summary: evidenceById.has(evidenceId) ? "Declared by the source Agent; independently audited below." : "Declared Evidence does not exist in the supplied package.",
  })));
  return { claims, evidence: [...new Map(evidence.map((item) => [item.evidence_id, item])).values()], supportLinks, reports };
}

export function normalizedTopic(claim) { return topicFor(claim); }
