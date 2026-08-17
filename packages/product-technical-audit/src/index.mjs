import { createHash } from "node:crypto";
import { normalizeReportSources } from "../../_shared/report-source-normalization.mjs";

const AGENT_CODE = "product-engineering";

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
  if (!normalized) throw new Error("observation and action keys must contain a stable identifier");
  return normalized;
}

function decisionRelevance(value) {
  return ["CRITICAL", "IMPORTANT", "CONTEXT"].includes(value) ? value : "IMPORTANT";
}

function sourceClaim(observation, index) {
  const claimId = `claim-product-${slug(observation.key)}`;
  const sources = normalizeReportSources(observation.sources);
  const admitted = sources.some((source) => source.supportRole === "SUPPORT");
  const status = admitted ? "VERIFIED" : "PENDING_VALIDATION";
  return {
    claim: {
      claim_id: claimId,
      section: observation.section,
      text: observation.text,
      status,
      decision_relevance: decisionRelevance(observation.decision_relevance),
      citation_ids: sources.map((_, sourceIndex) => `citation-product-${slug(observation.key)}-${sourceIndex + 1}`),
      score_bearing: admitted,
    },
    citations: sources.map((source, sourceIndex) => ({
      citation_id: `citation-product-${slug(observation.key)}-${sourceIndex + 1}`,
      claim_id: claimId,
      evidence_id: source.directory.evidence_id,
      source_locator_id: source.directory.source_locator_id,
      support_role: source.supportRole,
      audit_status: "VERIFIED",
      label: index + sourceIndex + 1,
    })),
    sources: sources.map((source) => source.directory),
  };
}

function action(value, index, claims) {
  const related = value.related_claim_ids?.length ? value.related_claim_ids : claims.filter((item) => !item.score_bearing).map((item) => item.claim_id);
  return {
    action_id: `action-product-${slug(value.key ?? String(index + 1))}`,
    title: value.title ?? "补齐产品运行证据并复验阶段门",
    owner: value.owner ?? "产品负责人",
    deadline_days: value.deadline_days ?? 14,
    success_criteria: value.success_criteria ?? ["同一核心流程复验通过且留下可追溯运行证据"],
    failure_triggers: value.failure_triggers ?? ["核心流程仍不可重复完成"],
    required_evidence: value.required_evidence ?? ["代码、运行日志或可复验浏览器记录"],
    related_claim_ids: related.length ? related : [claims[0].claim_id],
  };
}

export function buildProductTechnicalReport(input) {
  if (!input?.identity || !Array.isArray(input.observations) || input.observations.length === 0) throw new Error("identity and observations are required");
  const normalized = input.observations.map(sourceClaim);
  const claims = normalized.map((item) => item.claim);
  const sourceDirectory = [...new Map(normalized.flatMap((item) => item.sources).map((item) => [item.source_locator_id, item])).values()];
  const citations = normalized.flatMap((item) => item.citations).map((item, index) => ({...item, label: index + 1}));
  const actions = (input.actions?.length ? input.actions : [{}]).map((item, index) => action(item, index, claims));
  const pending = claims.filter((item) => item.status === "PENDING_VALIDATION").length;
  return {
    schema_version: "2.0",
    ...input.identity,
    agent_code: AGENT_CODE,
    source_sha256: sha(input),
    executive_summary: claims.slice(0, 3).map((item) => item.claim_id),
    metrics: [
      {key: "product_stage", label: "产品阶段", value: input.stage, claim_ids: claims.slice(0, 1).map((item) => item.claim_id)},
      {key: "pending_validation", label: "待验证判断", value: pending, claim_ids: claims.filter((item) => !item.score_bearing).map((item) => item.claim_id)},
    ],
    claims,
    domain_payload: {stage: input.stage, stage_gates: input.stage_gates, core_flows: input.core_flows, delivery_risks: input.delivery_risks, bus_factor: input.bus_factor},
    risks: claims.filter((item) => item.status !== "VERIFIED").map((item) => item.claim_id),
    actions,
    citations,
    source_directory: sourceDirectory,
    audit_summary: {verified: claims.length - pending, insufficient: 0, needs_more: pending, conflicted: 0},
    raw_audit_refs: [],
  };
}

export function selectProductTechnicalReport(report, view = "summary") {
  if (!["summary", "full"].includes(view)) throw new Error("view must be summary or full");
  const claimIds = view === "summary" ? report.executive_summary : report.claims.map((item) => item.claim_id);
  return {schema_version: report.schema_version, report_id: report.report_id, source_sha256: report.source_sha256, view, claim_ids: claimIds};
}
