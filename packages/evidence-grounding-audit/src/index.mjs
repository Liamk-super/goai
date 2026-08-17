import { createHash } from "node:crypto";
import { adaptAgentResults, normalizedTopic } from "./adapters.mjs";
import { renderFull, renderSummary } from "./report-renderer.mjs";

const EXPECTED = ["product", "user", "investment"];
const VERDICTS = new Set(["PASS", "DOWNGRADE", "REQUEST_MORE_EVIDENCE", "REJECT"]);

function hash(value) { return createHash("sha256").update(JSON.stringify(value)).digest("hex"); }
function uniq(values) { return [...new Set(values)]; }
function level(value) { return Math.max(0, Math.min(5, Number(String(value ?? "E0").replace("E", "")) || 0)); }
function traceable(e) { return e.source && e.source !== "untraceable" && e.source_tier !== "untraceable"; }
function parseDate(value) { const date = value && value !== "unknown" ? new Date(value) : null; return date && !Number.isNaN(date.valueOf()) ? date : null; }
function semanticMap(input, analyzer, claims, evidence) {
  const supplied = Object.fromEntries((input.semantic_analysis ?? []).map((item) => [item.claim_id, item]));
  const generated = analyzer ? analyzer({ claims: structuredClone(claims), evidence: structuredClone(evidence) }) : [];
  return new Map([...(Array.isArray(generated) ? generated : []), ...Object.values(supplied)].map((item) => [item.claim_id, item]));
}

function inferenceCodes(claim, semantic) {
  const raw = claim.text.toLowerCase();
  const codes = [...(semantic?.reason_codes ?? [])];
  if (semantic?.over_inference) codes.push("over_inference");
  if ((/市场|industry|market/u.test(raw) && /一定|必然|成功|company growth|产品成功/u.test(raw)) ||
      (/用户增长|user growth/u.test(raw) && /留存|付费|成功/u.test(raw)) ||
      (/第三方|估算|estimate/u.test(raw) && /官方|审计|audited|official/u.test(raw))) codes.push("over_inference");
  if (/第三方|估算|estimate/u.test(raw) && /官方|审计|audited|official/u.test(raw)) codes.push("source_misrepresented");
  if (/真实用户|用户非常需要|强烈需求|real users? definitely/u.test(raw)) codes.push("strong_user_claim");
  return uniq(codes);
}

function sourceKey(evidence) { return evidence.independence_group || evidence.original_source || evidence.content_fingerprint || `${evidence.publisher}|${evidence.source}`; }

function calibrateClaim(claim, evidenceById, semantic, now) {
  const cited = claim.evidence_refs.map((id) => evidenceById.get(id)).filter(Boolean);
  const missing = claim.evidence_refs.filter((id) => !evidenceById.has(id));
  const codes = inferenceCodes(claim, semantic);
  const identityMismatch = cited.some((e) => e.project_id !== claim.project_id || e.product_version !== claim.product_version);
  const expired = cited.filter((e) => e.superseded || (parseDate(e.expiry) && parseDate(e.expiry) < now));
  const untraceable = cited.filter((e) => !traceable(e));
  const integrity = cited.some((e) => e.integrity_issue);
  const maxLevel = Math.max(0, ...cited.map((e) => level(e.reliability_level)));
  const simulationOnly = cited.length > 0 && cited.every((e) => e.simulated || level(e.reliability_level) <= 2);
  const selfClaimOnly = cited.length > 0 && cited.every((e) => level(e.reliability_level) === 0);
  const direct = semantic?.support_strength !== "indirect" && semantic?.support_strength !== "none";
  const publicClaim = ["market_growth", "mau"].includes(claim.normalized_topic)
    || cited.some((e) => ["PUBLIC_RESEARCH", "PUBLIC_URL", "BROWSER", "SEARCH_RESULT"].includes(e.source_type));
  const sourceLocatorIds = uniq(cited.flatMap((e) => e.source_locator_ids ?? []));
  const hasInternalSupport = cited.some((e) => ["MATERIAL", "MATERIAL_UNIT", "UPLOAD", "INTERNAL_MATERIAL"].includes(e.source_type));
  const missingPublicLocator = publicClaim && sourceLocatorIds.length === 0 && !hasInternalSupport;
  let verdict = "PASS";
  const reasons = [];
  if (claim.evidence_refs.length === 0) { verdict = "REJECT"; reasons.push("missing_evidence_refs"); }
  else if (missing.length || untraceable.length) { verdict = "REJECT"; reasons.push(missing.length ? "evidence_not_found" : "source_untraceable"); }
  else if (identityMismatch) { verdict = "REJECT"; reasons.push("identity_mismatch"); }
  else if (integrity) { verdict = "REJECT"; reasons.push("integrity_issue"); }
  else if (codes.includes("source_misrepresented")) { verdict = "REJECT"; reasons.push("source_misrepresented"); }
  else if (codes.includes("over_inference") && claim.criticality === "critical") { verdict = "REQUEST_MORE_EVIDENCE"; reasons.push("over_inference"); }
  else if (codes.includes("over_inference")) { verdict = "DOWNGRADE"; reasons.push("over_inference"); }
  else if (missingPublicLocator) { verdict = "REQUEST_MORE_EVIDENCE"; reasons.push("source_locator_missing"); }
  else if (expired.length) { verdict = traceable(expired[0]) ? "REQUEST_MORE_EVIDENCE" : "REJECT"; reasons.push(expired.some((e) => e.superseded) ? "superseded" : "expired"); }
  else if (selfClaimOnly) { verdict = "REJECT"; reasons.push("team_statement_only"); }
  else if (simulationOnly || maxLevel <= 2 || !direct) { verdict = "DOWNGRADE"; reasons.push(simulationOnly ? "simulation_or_low_level_only" : "indirect_support"); }
  if (codes.includes("strong_user_claim") && simulationOnly) reasons.push("simulation_mislabeled");
  const evidenceStrength = [8, 16, 24, 32, 40, 40][maxLevel];
  const sourceReliability = cited.length
    ? Math.max(...cited.map((e) => ({ tier_1: 20, tier_2: 16, tier_3: 8 }[e.source_tier] ?? 4)))
    : 0;
  const freshness = expired.length ? 8 : 20;
  const reasoningQuality = codes.includes("over_inference") ? 4 : direct ? 20 : 12;
  const score = Math.min(100, evidenceStrength + sourceReliability + freshness + reasoningQuality);
  const rule = { PASS: "KB-EVD-D01", DOWNGRADE: "KB-EVD-D02", REJECT: "KB-EVD-D03", REQUEST_MORE_EVIDENCE: "KB-EVD-D04" }[verdict];
  const calibratedText = verdict === "DOWNGRADE"
    ? `当前材料显示“${claim.text}”可能成立，但证据强度或适用范围有限，仍需进一步验证。`
    : verdict === "PASS" ? claim.text : null;
  const requiredEvidence = verdict === "REQUEST_MORE_EVIDENCE" ? [`补充与 ${claim.normalized_topic} 同项目、同版本、同范围的可追溯直接证据。`] : [];
  const independentSourceCount = new Set(cited.map(sourceKey)).size;
  const freshnessStatus = cited.some((e) => e.superseded) ? "SUPERSEDED" : expired.length ? "EXPIRED" : "VALID";
  const freshnessScore = freshnessStatus === "VALID" ? 1 : freshnessStatus === "EXPIRED" ? 0.25 : 0;
  const supportStrength = cited.length === 0 || verdict === "REJECT" ? "NONE"
    : maxLevel >= 4 && direct ? "STRONG" : maxLevel >= 3 && direct ? "MODERATE" : "WEAK";
  const citationStatus = verdict === "PASS" ? "VERIFIED" : verdict === "DOWNGRADE" ? "DOWNGRADED"
    : verdict === "REQUEST_MORE_EVIDENCE" ? "PENDING_VALIDATION" : "REJECTED";
  const scoreBearing = ["VERIFIED", "DOWNGRADED"].includes(citationStatus) && freshnessStatus === "VALID";
  return {
    claim_id: claim.claim_id, verdict, confidence: Number((score / 100).toFixed(2)),
    reason_codes: uniq([rule, ...reasons]), audit_summary: reasons.join(", ") || "Direct, traceable and scope-matched support.",
    calibrated_text: calibratedText, evidence_refs: cited.map((e) => e.evidence_id), required_evidence: requiredEvidence,
    score_components: { evidence_strength: evidenceStrength, source_reliability: sourceReliability, freshness, reasoning_quality: reasoningQuality, total: score },
    support_strength: supportStrength, independent_source_count: independentSourceCount,
    freshness_status: freshnessStatus, freshness_score: freshnessScore,
    evidence_ids: cited.map((e) => e.evidence_id), source_locator_ids: sourceLocatorIds,
    citation_status: citationStatus, score_bearing: scoreBearing,
    integrity_issue: integrity,
  };
}

function canonicalize(claims, decisions, evidenceById, semantics) {
  const byDecision = new Map(decisions.map((item) => [item.claim_id, item]));
  const groups = new Map();
  for (const claim of claims) {
    const topic = semantics.get(claim.claim_id)?.duplicate_topic || normalizedTopic(claim);
    if (!groups.has(topic)) groups.set(topic, []);
    groups.get(topic).push(claim);
  }
  return [...groups.entries()].map(([topic, group], index) => {
    const refs = uniq(group.flatMap((claim) => claim.evidence_refs));
    const independent = new Set(refs.map((id) => evidenceById.get(id)).filter(Boolean).map(sourceKey));
    const verdicts = group.map((claim) => byDecision.get(claim.claim_id)?.verdict);
    const verdict = verdicts.includes("REJECT") ? (verdicts.some((v) => v === "PASS" || v === "DOWNGRADE") ? "DOWNGRADE" : "REJECT")
      : verdicts.includes("REQUEST_MORE_EVIDENCE") ? "REQUEST_MORE_EVIDENCE" : verdicts.includes("DOWNGRADE") ? "DOWNGRADE" : "PASS";
    return {
      canonical_claim_id: `CC-${String(index + 1).padStart(3, "0")}`, normalized_topic: topic,
      representative_text: group[0].text, merged_claim_ids: group.map((claim) => claim.claim_id),
      source_agents: uniq(group.map((claim) => claim.source_agent)), agent_count: uniq(group.map((claim) => claim.source_agent)).length,
      unique_evidence_sources: [...independent], independent_evidence_count: independent.size,
      verdict, confidence: Math.min(...group.map((claim) => byDecision.get(claim.claim_id)?.confidence ?? 0)), evidence_refs: refs,
    };
  });
}

function detectConflicts(claims, semantics) {
  const conflicts = [];
  const byTopic = new Map();
  for (const claim of claims) {
    if (!byTopic.has(claim.normalized_topic)) byTopic.set(claim.normalized_topic, []);
    byTopic.get(claim.normalized_topic).push(claim);
  }
  for (const [topic, group] of byTopic) {
    if (group.length < 2) continue;
    if (topic === "product_stage") {
      const positions = uniq(group.map((claim) => String(semantics.get(claim.claim_id)?.position ?? claim.value ?? claim.text).match(/early_product|mature_operation/iu)?.[0]).filter(Boolean));
      if (positions.includes("early_product") && positions.includes("mature_operation")) conflicts.push(makeConflict(conflicts.length, topic, group, "stage_conflict", "incompatible product stage assertions"));
    }
    if (topic === "mau") {
      const fields = ["metric_definition", "time_scope", "geography", "population", "method", "product_version"];
      const complete = group.every((claim) => fields.every((field) => claim[field] !== null && claim[field] !== ""));
      const aligned = complete && fields.every((field) => new Set(group.map((claim) => JSON.stringify(claim[field]))).size === 1);
      const values = uniq(group.map((claim) => Number(semantics.get(claim.claim_id)?.position ?? claim.value)).filter(Number.isFinite));
      if (!complete || !aligned) conflicts.push(makeConflict(conflicts.length, topic, group, !complete ? "metric_definition_gap" : "scope_gap", "numeric definitions must be aligned before comparison"));
      else if (values.length > 1) conflicts.push(makeConflict(conflicts.length, topic, group, "data_conflict", "same-scope numeric values differ"));
    }
  }
  return conflicts;
}

function makeConflict(index, topic, group, type, rootCause) {
  return { conflict_id: `CF-${String(index + 1).padStart(3, "0")}`, topic, involved_claim_ids: group.map((c) => c.claim_id), involved_agents: uniq(group.map((c) => c.source_agent)), conflict_type: type, root_cause: rootCause, severity: "high", resolution_status: "UNRESOLVED", recommended_action: "Align provenance, scope, metric definition, and version; do not silently choose a side." };
}

function detectTensions(claims, decisions) {
  const byId = new Map(decisions.map((d) => [d.claim_id, d]));
  const userWeak = claims.find((c) => c.source_agent === "user-evidence" && c.normalized_topic === "user_demand" && byId.get(c.claim_id)?.verdict !== "PASS");
  const investmentPositive = claims.find((c) => c.source_agent === "business-investment" && c.normalized_topic === "investment_potential" && !/无|低|not|weak/iu.test(c.text));
  return userWeak && investmentPositive ? [{
    tension_id: "DT-001", topic: "demand_evidence_vs_investment_potential", involved_agents: [userWeak.source_agent, investmentPositive.source_agent],
    position_a: userWeak.text, position_b: investmentPositive.text, why_not_direct_conflict: "Both may be true: investment optionality can coexist with weak current demand evidence.",
    implication_for_supervisor: "Explain why continued investment is justified while real-user validation remains weak.",
  }] : [];
}

function validateInput(input) {
  if (!input || typeof input !== "object") return "input must be an object";
  for (const field of ["task_id", "project_id", "product_version", "generated_at"]) if (!input[field]) return `${field} is required`;
  if (!Array.isArray(input.agent_results) || input.agent_results.length === 0) return "agent_results must not be empty";
  return null;
}

export function runAudit(input, { semanticAnalyzer = null } = {}) {
  const invalid = validateInput(input);
  if (invalid) return { task_id: input?.task_id ?? "invalid-task", status: "blocked", result_summary: invalid, structured_output: null, evidence_refs: [], confidence: 0, risks: [invalid], needs_human_review: false, failure_reason: "invalid_input_schema", retryable: false };
  const adapted = adaptAgentResults(input);
  const evidenceById = new Map(adapted.evidence.map((item) => [item.evidence_id, item]));
  const semantics = semanticMap(input, semanticAnalyzer, adapted.claims, adapted.evidence);
  const now = new Date(input.generated_at);
  const decisions = adapted.claims.map((claim) => calibrateClaim(claim, evidenceById, semantics.get(claim.claim_id), now));
  if (decisions.some((item) => !VERDICTS.has(item.verdict))) throw new Error("invalid calibration verdict");
  const canonicalClaims = canonicalize(adapted.claims, decisions, evidenceById, semantics);
  const conflicts = detectConflicts(adapted.claims, semantics);
  const tensions = detectTensions(adapted.claims, decisions);
  const expected = input.expected_agents ?? EXPECTED;
  const completed = input.agent_results.filter((item) => item.status === "COMPLETED").map((item) => item.source_agent);
  const missingAgents = expected.filter((agent) => !completed.includes(agent));
  const gaps = decisions.filter((item) => item.verdict === "REQUEST_MORE_EVIDENCE").map((item, index) => ({ gap_id: `GAP-${String(index + 1).padStart(3, "0")}`, affected_claim_ids: [item.claim_id], missing_evidence: item.required_evidence, why_it_matters: "The claim is decision-critical but not currently admissible.", recommended_owner: adapted.claims.find((claim) => claim.claim_id === item.claim_id)?.source_agent ?? "human_operator", target_evidence_level: "E3+" }));
  if (missingAgents.length) gaps.push({ gap_id: `GAP-${String(gaps.length + 1).padStart(3, "0")}`, affected_claim_ids: [], missing_evidence: missingAgents.map((agent) => `${agent} Agent result`), why_it_matters: "Audit coverage is partial.", recommended_owner: "evaluation-manager", target_evidence_level: "completed structured handoff" });
  const crossAgentIssues = canonicalClaims.filter((item) => item.agent_count > 1).map((item, index) => ({ issue_id: `ISSUE-${String(index + 1).padStart(3, "0")}`, topic: item.normalized_topic, canonical_claim_id: item.canonical_claim_id, agent_count: item.agent_count, independent_evidence_count: item.independent_evidence_count, role_impacts: item.source_agents.map((agent) => ({ source_agent: agent, impact: adapted.claims.find((c) => c.source_agent === agent && item.merged_claim_ids.includes(c.claim_id))?.text })) }));
  const accepted = canonicalClaims.filter((item) => item.verdict === "PASS");
  const downgraded = canonicalClaims.filter((item) => item.verdict === "DOWNGRADE");
  const rejected = canonicalClaims.filter((item) => item.verdict === "REJECT");
  const audited = decisions.length || 1;
  const reliabilityScore = decisions.reduce((sum, item) => sum + item.confidence, 0) / audited;
  const overallReliability = missingAgents.length || decisions.some((item) => item.verdict === "REJECT") ? (reliabilityScore >= 0.5 ? "MEDIUM" : "LOW") : reliabilityScore >= 0.75 ? "HIGH" : "MEDIUM";
  const supervisor = {
    overall_reliability: overallReliability, audit_coverage: { expected_agents: expected, completed_agents: completed, missing_agents: missingAgents, partial_audit: missingAgents.length > 0 },
    canonical_claims: canonicalClaims, accepted_claims: accepted, downgraded_claims: downgraded, rejected_claims: rejected,
    evidence_gaps: gaps, conflicts, decision_tensions: tensions, cross_agent_issues: crossAgentIssues,
    key_unknowns: gaps.flatMap((gap) => gap.missing_evidence).slice(0, 10), evidence_refs: uniq(adapted.evidence.map((item) => item.evidence_id)),
    report_refs: { summary: "evidence_calibration_summary.html", full: "evidence_calibration_full.html" },
  };
  assertSupervisorHandoff(supervisor);
  const base = { meta: { skill: "evidence-grounding-audit", version: "2.1.0", generated_at: input.generated_at, source_reports: adapted.reports }, claims: adapted.claims, evidence: adapted.evidence, support_links: adapted.supportLinks, calibration_decisions: decisions, canonical_claims: canonicalClaims, conflicts, decision_tensions: tensions, evidence_gaps: gaps, cross_agent_issues: crossAgentIssues, supervisor_handoff: supervisor };
  const digest = hash(base);
  const reports = { structured_output_digest: digest, summary_html: renderSummary(base, digest), full_html: renderFull(base, digest) };
  const structured = { ...base, reports, structured_output_digest: digest };
  return { task_id: input.task_id, status: "completed", result_summary: `Audited ${decisions.length} claims; ${accepted.length} canonical claims passed, ${downgraded.length} downgraded, ${rejected.length} rejected.`, structured_output: structured, evidence_refs: supervisor.evidence_refs, confidence: Number(reliabilityScore.toFixed(2)), risks: uniq([...conflicts.map((item) => item.conflict_type), ...gaps.map((item) => item.why_it_matters)]), needs_human_review: decisions.some((item) => item.integrity_issue), failure_reason: null, retryable: false };
}

export function assertSupervisorHandoff(handoff) {
  if (handoff.accepted_claims.some((item) => item.verdict === "REJECT")) throw new Error("REJECT claim entered accepted claims");
  if (handoff.accepted_claims.some((item) => item.verdict === "REQUEST_MORE_EVIDENCE")) throw new Error("REQUEST claim entered accepted claims");
  return true;
}

export { renderFull, renderSummary } from "./report-renderer.mjs";
export {
  AUDIT_LABELS_ZH,
  buildEvidenceSpecialistReportV2,
  selectEvidenceSpecialistReportV2,
} from "./specialist-report.mjs";
