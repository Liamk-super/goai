import { createHash } from "node:crypto";
import { normalizeReportSources } from "../../_shared/report-source-normalization.mjs";

const AGENT_CODE = "business-investment";
const PROVENANCE_SECTIONS = new Set(["MARKET", "COMPETITION", "LEGAL", "COMPLIANCE"]);

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  return value;
}

function sha(value) {
  return createHash("sha256").update(JSON.stringify(canonical(value))).digest("hex");
}

function slug(value) {
  const normalized = String(value).trim().replace(/[^A-Za-z0-9._-]+/gu, "-").replace(/^-+|-+$/gu, "");
  if (!normalized) throw new Error("claim keys must contain a stable identifier");
  return normalized;
}

function decisionRelevance(value) {
  return ["CRITICAL", "IMPORTANT", "CONTEXT"].includes(value) ? value : "IMPORTANT";
}

function normalizeObservation(observation) {
  const claimId = `claim-investment-${slug(observation.key)}`;
  const sources = normalizeReportSources(observation.sources);
  const missingRequiredProvenance = PROVENANCE_SECTIONS.has(observation.section) && sources.some((source) => !source.directory.region || !source.directory.fetched_at);
  const admitted = sources.some((source) => source.supportRole === "SUPPORT") && !missingRequiredProvenance;
  return {
    claim: {claim_id: claimId, section: observation.section, text: observation.text, status: admitted ? "VERIFIED" : "PENDING_VALIDATION", decision_relevance: decisionRelevance(observation.decision_relevance), citation_ids: sources.map((_, index) => `citation-investment-${slug(observation.key)}-${index + 1}`), score_bearing: admitted},
    citations: sources.map((source, index) => ({citation_id: `citation-investment-${slug(observation.key)}-${index + 1}`, claim_id: claimId, evidence_id: source.directory.evidence_id, source_locator_id: source.directory.source_locator_id, support_role: source.supportRole, audit_status: "VERIFIED", label: index + 1})),
    sources: sources.map((source) => source.directory),
  };
}

function normalizeAction(value, index, claims) {
  const pending = claims.filter((item) => !item.score_bearing).map((item) => item.claim_id);
  return {action_id: `action-investment-${slug(value.key ?? String(index + 1))}`, title: value.title ?? "补齐商业假设的真实数据", owner: value.owner ?? "项目负责人", deadline_days: value.deadline_days ?? 21, success_criteria: value.success_criteria ?? ["关键单位经济输入由可追溯数据支持"], failure_triggers: value.failure_triggers ?? ["验证后仍无法形成正向或可控区间"], required_evidence: value.required_evidence ?? ["支付、成本、获客或续费的真实记录"], related_claim_ids: value.related_claim_ids?.length ? value.related_claim_ids : pending.length ? pending : [claims[0].claim_id]};
}

export function buildBusinessInvestmentReport(input) {
  if (!input?.identity || !Array.isArray(input.observations) || input.observations.length === 0) throw new Error("identity and observations are required");
  const normalized = input.observations.map(normalizeObservation);
  const claims = normalized.map((item) => item.claim);
  const citations = normalized.flatMap((item) => item.citations).map((item, index) => ({...item, label: index + 1}));
  const sourceDirectory = [...new Map(normalized.flatMap((item) => item.sources).map((item) => [item.source_locator_id, item])).values()];
  const actions = (input.actions?.length ? input.actions : [{}]).map((item, index) => normalizeAction(item, index, claims));
  const pending = claims.filter((item) => !item.score_bearing).length;
  const metrics = Object.entries(input.unit_economics ?? {}).slice(0, 20).map(([key, value]) => ({key: `unit_${key}`, label: key, value: typeof value === "object" ? JSON.stringify(value) : value, claim_ids: claims.filter((item) => item.section === "UNIT_ECONOMICS").map((item) => item.claim_id)}));
  return {schema_version: "2.0", ...input.identity, agent_code: AGENT_CODE, source_sha256: sha(input), executive_summary: claims.slice(0, 3).map((item) => item.claim_id), metrics: [...metrics, {key: "pending_validation", label: "待验证判断", value: pending, claim_ids: claims.filter((item) => !item.score_bearing).map((item) => item.claim_id)}], claims, domain_payload: {business_model: input.business_model, unit_economics: input.unit_economics, investment_gates: input.investment_gates}, risks: claims.filter((item) => !item.score_bearing).map((item) => item.claim_id), actions, citations, source_directory: sourceDirectory, audit_summary: {verified: claims.length - pending, insufficient: 0, needs_more: pending, conflicted: 0}, raw_audit_refs: []};
}

export function selectBusinessInvestmentReport(report, view = "summary") {
  if (!["summary", "full"].includes(view)) throw new Error("view must be summary or full");
  return {schema_version: report.schema_version, report_id: report.report_id, source_sha256: report.source_sha256, view, claim_ids: view === "summary" ? report.executive_summary : report.claims.map((item) => item.claim_id)};
}
