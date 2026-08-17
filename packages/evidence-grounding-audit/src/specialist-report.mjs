import { createHash } from "node:crypto";

export const AUDIT_LABELS_ZH = Object.freeze({
  VERIFIED: "证据充分",
  DOWNGRADED: "证据有限，已降低确定性",
  PENDING_VALIDATION: "待补充证据",
  REJECTED: "当前证据不支持",
  CONFLICTED: "来源存在冲突",
});

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  return value;
}

function slug(value) {
  const normalized = String(value).trim().replace(/^claim-/u, "").replace(/[^A-Za-z0-9._-]+/gu, "-").replace(/^-+|-+$/gu, "");
  if (!normalized) throw new Error("audit claim keys must contain a stable identifier");
  return normalized;
}

export function buildEvidenceSpecialistReportV2({ identity, auditResult, sourceDirectory = [] }) {
  const structured = auditResult?.structured_output ?? auditResult;
  if (!identity || !structured || !Array.isArray(structured.calibration_decisions)) throw new Error("identity and calibrated audit output are required");
  const claimById = new Map((structured.claims ?? []).map((item) => [item.claim_id, item]));
  const sourceById = new Map(sourceDirectory.map((item) => [item.source_locator_id, item]));
  const claims = structured.calibration_decisions.map((decision) => {
    const claimId = `claim-audit-${slug(decision.claim_id)}`;
    const source = claimById.get(decision.claim_id);
    const status = decision.citation_status === "REJECTED" ? "PENDING_VALIDATION" : decision.citation_status;
    return {claim_id: claimId, section: "EVIDENCE_CALIBRATION", text: decision.calibrated_text ?? source?.text ?? decision.audit_summary ?? AUDIT_LABELS_ZH[status], status, decision_relevance: source?.criticality === "critical" ? "CRITICAL" : "IMPORTANT", citation_ids: decision.score_bearing ? (decision.evidence_ids ?? []).map((_, index) => `citation-audit-${slug(decision.claim_id)}-${index + 1}`) : [], score_bearing: Boolean(decision.score_bearing)};
  });
  const citations = [];
  structured.calibration_decisions.forEach((decision) => {
    if (!decision.score_bearing) return;
    (decision.evidence_ids ?? []).forEach((evidenceId, index) => citations.push({citation_id: `citation-audit-${slug(decision.claim_id)}-${index + 1}`, claim_id: `claim-audit-${slug(decision.claim_id)}`, evidence_id: evidenceId, source_locator_id: decision.source_locator_ids?.[index] ?? null, support_role: "SUPPORT", audit_status: decision.citation_status === "DOWNGRADED" ? "DOWNGRADED" : "VERIFIED", label: citations.length + 1}));
  });
  const usedSources = [...new Set(structured.calibration_decisions.flatMap((item) => item.source_locator_ids ?? []))].map((id) => sourceById.get(id)).filter(Boolean);
  const counts = {verified: 0, insufficient: 0, needs_more: 0, conflicted: structured.conflicts?.length ?? 0};
  for (const item of structured.calibration_decisions) {
    if (item.citation_status === "VERIFIED") counts.verified += 1;
    else if (item.citation_status === "DOWNGRADED") counts.insufficient += 1;
    else counts.needs_more += 1;
  }
  const pending = claims.filter((item) => !item.score_bearing);
  const actions = (structured.evidence_gaps?.length ? structured.evidence_gaps : [{gap_id: "general", missing_evidence: ["补齐待验证判断的直接证据"], affected_claim_ids: pending.map((item) => item.claim_id)}]).map((gap, index) => ({action_id: `action-audit-${slug(gap.gap_id ?? String(index + 1))}`, title: `补证：${(gap.missing_evidence ?? ["待验证判断"])[0]}`, owner: gap.recommended_owner ?? "项目负责人", deadline_days: 14, success_criteria: ["新证据可追溯且与判断的项目、版本、地区和时间范围一致"], failure_triggers: ["补证后仍无直接支持或来源冲突未解决"], required_evidence: gap.missing_evidence ?? ["可追溯直接证据"], related_claim_ids: (gap.affected_claim_ids ?? []).map((id) => `claim-audit-${slug(id)}`).filter((id) => claims.some((claim) => claim.claim_id === id)).length ? (gap.affected_claim_ids ?? []).map((id) => `claim-audit-${slug(id)}`).filter((id) => claims.some((claim) => claim.claim_id === id)) : [claims[0].claim_id]}));
  const sourceSha256 = createHash("sha256").update(JSON.stringify(canonical({identity, auditResult, sourceDirectory}))).digest("hex");
  return {schema_version: "2.0", ...identity, agent_code: "evidence-auditor", source_sha256: sourceSha256, executive_summary: claims.slice(0, 3).map((item) => item.claim_id), metrics: Object.entries(counts).map(([key, value]) => ({key, label: {verified: "证据充分", insufficient: "证据有限", needs_more: "待补证", conflicted: "存在冲突"}[key], value, claim_ids: []})), claims, domain_payload: {labels: AUDIT_LABELS_ZH, calibration_decisions: structured.calibration_decisions, conflicts: structured.conflicts ?? [], evidence_gaps: structured.evidence_gaps ?? []}, risks: pending.map((item) => item.claim_id), actions, citations, source_directory: usedSources, audit_summary: counts, raw_audit_refs: structured.calibration_decisions.map((item) => item.claim_id)};
}

export function selectEvidenceSpecialistReportV2(report, view = "summary") {
  if (!["summary", "full"].includes(view)) throw new Error("view must be summary or full");
  return {schema_version: report.schema_version, report_id: report.report_id, source_sha256: report.source_sha256, view, claim_ids: view === "summary" ? report.executive_summary : report.claims.map((item) => item.claim_id)};
}
