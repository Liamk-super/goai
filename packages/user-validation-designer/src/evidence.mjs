/**
 * Evidence discipline. Implements A-01 / A-02 and the KB global convention
 * "模拟上限 E2".
 *
 * Two directions of traffic, deliberately kept apart:
 *
 *   ISSUE   cards this skill creates from its own simulation. Always E0-E2.
 *   INGEST  real user evidence supplied by the caller. Tiers preserved (E0-E5),
 *           but NOT re-issued as this skill's evidence cards.
 *
 * That separation is the point of the E2 ceiling. An AI persona is not a user
 * and a simulated quote is not something a person said, so this skill must
 * never appear as the source of E3+ evidence. Real evidence is referenced and
 * scored, never re-badged. schema/evidence-card.schema.json enforces the
 * ceiling structurally: reliability_level only admits E0, E1, E2.
 */

import { createHash } from "node:crypto";
import { clampSimulatedTier, isRealUserTier, tierOrdinal, SIMULATION_CEILING } from "./rules.mjs";

/** Fixed disclaimer, injected by program so the model cannot soften it (A-02). */
export const SIMULATION_DISCLAIMER =
  "本报告中所有 Persona、模拟访谈、模拟体验与任务测试结果均为 AI 模拟产出，证据等级封顶 E2，不得作为真实用户结论使用；真实用户证据（E3+）永远优先。";

export const KIND_TIER_CEILING = Object.freeze({
  team_statement: "E0",
  review: "E1",
  public_comment: "E1",
  interview: "E3",
  survey: "E3",
  usability_test: "E3",
  usage_data: "E4",
  retention_data: "E4",
  payment_record: "E5",
  contract: "E5",
});

export const KIND_ALLOWED_DIMENSIONS = Object.freeze({
  team_statement: [],
  review: ["demand_strength", "pain_severity", "alternative_gap"],
  public_comment: ["demand_strength", "pain_severity", "alternative_gap"],
  interview: ["demand_strength", "pain_severity", "alternative_gap", "willingness_to_pay"],
  survey: ["demand_strength"],
  usability_test: ["pain_severity"],
  usage_data: ["usage_frequency"],
  retention_data: ["usage_frequency"],
  payment_record: ["willingness_to_pay"],
  contract: ["willingness_to_pay"],
});

export const KIND_ALLOWED_CLAIM_TYPES = Object.freeze({
  team_statement: [],
  review: ["demand", "alternative"],
  public_comment: ["demand", "alternative"],
  interview: ["demand", "behavior", "willingness_to_pay", "segment", "alternative"],
  survey: ["demand", "segment"],
  usability_test: ["usability", "behavior"],
  usage_data: ["behavior"],
  retention_data: ["retention", "behavior"],
  payment_record: ["willingness_to_pay"],
  contract: ["willingness_to_pay"],
});

export const CLAIM_TYPE_ALLOWED_DIMENSIONS = Object.freeze({
  demand: ["demand_strength", "pain_severity", "alternative_gap"],
  behavior: ["demand_strength", "usage_frequency", "pain_severity", "alternative_gap"],
  willingness_to_pay: ["willingness_to_pay"],
  usability: ["pain_severity"],
  retention: ["usage_frequency"],
  segment: ["demand_strength", "usage_frequency", "pain_severity", "alternative_gap", "willingness_to_pay"],
  alternative: ["alternative_gap", "pain_severity", "demand_strength"],
});

const VERSION_STABLE_KINDS = new Set(["interview", "review", "public_comment"]);

const SOURCE_TIER_ORDER = Object.freeze(["untraceable", "tier_3", "tier_2", "tier_1"]);
export const KIND_SOURCE_TIER_CEILING = Object.freeze({
  team_statement: "tier_3",
  review: "tier_3",
  public_comment: "tier_3",
  interview: "tier_1",
  survey: "tier_1",
  usability_test: "tier_1",
  usage_data: "tier_1",
  retention_data: "tier_1",
  payment_record: "tier_1",
  contract: "tier_1",
});

function clampSourceTier(kind, claimed) {
  const ceiling = KIND_SOURCE_TIER_CEILING[kind] ?? "untraceable";
  const value = SOURCE_TIER_ORDER.includes(claimed) ? claimed : "untraceable";
  return SOURCE_TIER_ORDER.indexOf(value) > SOURCE_TIER_ORDER.indexOf(ceiling) ? ceiling : value;
}

export function assessSampleAdequacy(item) {
  const personaCount = Math.max(1, item?.applies_to_persona_ids?.length ?? 0);
  const recommended = item?.kind === "interview"
    ? 5 * personaCount
    : item?.kind === "usability_test"
      ? 5
      : item?.kind === "survey"
        ? 100
        : null;
  if (recommended === null) return { sample_adequacy: "not_applicable", recommended_min: null };
  if (!Number.isInteger(item?.sample_size)) return { sample_adequacy: "unknown", recommended_min: recommended };
  return {
    sample_adequacy: item.sample_size < recommended ? "underpowered" : "adequate",
    recommended_min: recommended,
  };
}

/**
 * NEW-DECISION-U09: content hash material for a simulated card.
 * Normalized join of unit + persona + text, so identical simulation output
 * hashes identically and the calibration agent can deduplicate.
 */
export function simulatedContentHash({ unit, personaId, text }) {
  const material = [unit ?? "", personaId ?? "", String(text ?? "").normalize("NFC").trim().replace(/\s+/gu, " ")].join(
    "\u001f",
  );
  return createHash("sha256").update(material, "utf8").digest("hex");
}

/**
 * Build an evidence card for a simulated observation.
 * reliability_level is forced to E2; the caller cannot raise it.
 */
export function buildSimulatedCard({
  evidence_id,
  evidence_type,
  unit,
  personaId,
  observation,
  source,
  timestamp,
  supporting_claims = [],
  product_version,
  scope,
  persona_ids = [],
  valid_for_dimensions = [],
  expiry = "unknown",
  source_tier = "tier_3",
}) {
  return {
    evidence_id,
    evidence_type,
    source: source ?? `simulation://${unit}/${personaId ?? "set"}`,
    source_tier,
    timestamp,
    reliability_level: SIMULATION_CEILING,
    supporting_claims,
    applicability: {
      product_version,
      scope: scope ?? unit,
      environment: null,
      persona_ids,
      segment: null,
      valid_for_dimensions,
    },
    expiry,
    content_hash: simulatedContentHash({ unit, personaId, text: observation }),
    observation,
    fact_type: "inference",
    simulation_note: "AI simulation output. Capped at E2; not a real user statement.",
  };
}

/**
 * A-01: clamp any card this skill emits, and record what was downgraded.
 * A model returning an E4 card is asserting real-world data it cannot have;
 * trusting it silently would corrupt every downstream judgment.
 */
export function clampIssuedCards(cards) {
  const downgraded = [];
  const clamped = (cards ?? []).map((card) => {
    const target = clampSimulatedTier(card?.reliability_level);
    if (target !== card?.reliability_level) {
      downgraded.push({
        ref: card?.evidence_id ?? "unknown",
        from_tier: card?.reliability_level,
        to_tier: target,
        reason: "simulation output cannot exceed E2 (KB global convention); tier forced down",
      });
      return { ...card, reliability_level: target };
    }
    return card;
  });
  return { cards: clamped, downgraded };
}

export function ingestedContentHash(item, effective = {}) {
  return createHash("sha256").update(JSON.stringify({
    kind: item?.kind ?? null,
    source: String(item?.source ?? "").normalize("NFC").trim(),
    timestamp: item?.timestamp ?? null,
    observation: String(item?.observation ?? "").normalize("NFC").trim().replace(/\s+/gu, " "),
    sample_size: Number.isInteger(item?.sample_size) ? item.sample_size : null,
    product_version: item?.applies_to_product_version ?? "unknown",
    version_stable: item?.version_stable === true,
    stable_reason: item?.stable_reason ?? null,
    persona_ids: [...(item?.applies_to_persona_ids ?? [])].sort(),
    segment: item?.applies_to_segment ?? null,
    dimensions: [...(effective.dimensions ?? item?.valid_for_dimensions ?? [])].sort(),
    supporting_claims: [...(item?.supporting_claims ?? [])].sort(),
    contradicting_claims: [...(item?.contradicts_claims ?? [])].sort(),
    effective_tier: effective.tier ?? item?.tier ?? "E0",
    source_tier: effective.source_tier ?? item?.source_tier ?? "untraceable",
  }), "utf8").digest("hex");
}

/** Program owns provenance, scope and hash for every simulated card. */
export function normalizeIssuedCards(cards, { unit, productVersion, timestamp, personaIds = [] } = {}) {
  const downgraded = [];
  const normalized = (cards ?? []).map((card, index) => {
    const requested = card?.reliability_level ?? "E2";
    if (tierOrdinal(requested) > tierOrdinal("E2")) {
      downgraded.push({ ref: card?.evidence_id ?? `issued-${index + 1}`, from_tier: requested, to_tier: "E2", reason: "simulation output cannot exceed E2" });
    }
    const cardPersonaIds = Array.isArray(card?.applicability?.persona_ids)
      ? card.applicability.persona_ids.filter((id) => personaIds.length === 0 || personaIds.includes(id))
      : [];
    const scope = unit ?? card?.applicability?.scope ?? "simulation";
    return buildSimulatedCard({
      evidence_id: card?.evidence_id ?? `EV-${scope}-${index + 1}`,
      evidence_type: card?.evidence_type ?? "simulation_observation",
      unit: scope,
      personaId: cardPersonaIds[0] ?? null,
      observation: String(card?.observation ?? "").normalize("NFC").trim(),
      timestamp: timestamp ?? card?.timestamp,
      supporting_claims: Array.isArray(card?.supporting_claims) ? card.supporting_claims : [],
      product_version: productVersion,
      scope,
      persona_ids: cardPersonaIds,
      valid_for_dimensions: Array.isArray(card?.applicability?.valid_for_dimensions) ? card.applicability.valid_for_dimensions : [],
      expiry: card?.expiry ?? "unknown",
      source_tier: "tier_3",
    });
  });
  return { cards: normalized, downgraded };
}

/**
 * Normalize caller-supplied evidence for INTERNAL scoring use.
 *
 * These records keep their real tier (E3/E4/E5) and are deliberately NOT put
 * into structured_output.evidence_cards, because this skill did not observe
 * them. They are referenced by id, counted in tier_distribution, and drive
 * has_real_user_evidence.
 */
export function ingestExistingEvidence(existing, { collected_at, product_version = null } = {}) {
  const records = [];
  const expiryUnknown = [];
  const downgraded = [];
  const diagnostics = [];
  const seen = new Set();

  for (const item of existing ?? []) {
    if (seen.has(item.evidence_id)) {
      diagnostics.push({ code: "duplicate_id", ref: item.evidence_id, detail: "duplicate evidence_id rejected" });
      continue;
    }
    seen.add(item.evidence_id);
    const ceiling = KIND_TIER_CEILING[item.kind] ?? "E0";
    const effectiveTier = tierOrdinal(item.tier) > tierOrdinal(ceiling) ? ceiling : item.tier;
    if (effectiveTier !== item.tier) downgraded.push({ ref: item.evidence_id, from_tier: item.tier, to_tier: effectiveTier, reason: `${item.kind} ceiling is ${ceiling}` });
    const timestampValid = typeof item.timestamp === "string" && Number.isFinite(Date.parse(item.timestamp));
    if (!timestampValid) diagnostics.push({ code: "invalid_timestamp", ref: item.evidence_id, detail: "timestamp is not parseable ISO8601" });
    const appliesVersion = item.applies_to_product_version ?? "unknown";
    const stableAllowed = item.version_stable === true && VERSION_STABLE_KINDS.has(item.kind) && typeof item.stable_reason === "string" && item.stable_reason.trim().length > 0;
    const versionMatches = !product_version || appliesVersion === product_version || stableAllowed;
    if (!versionMatches) diagnostics.push({ code: "product_version_mismatch", ref: item.evidence_id, detail: `${appliesVersion} does not apply to ${product_version}` });
    const expiry = typeof item.expiry === "string" && item.expiry.trim().length > 0 ? item.expiry : "unknown";
    if (expiry === "unknown") expiryUnknown.push(item.evidence_id);

    const inferredSourceTier = inferSourceTier(item);
    const claimedSourceTier = item.source_tier ?? inferredSourceTier;
    const provenanceBound = SOURCE_TIER_ORDER.indexOf(claimedSourceTier) > SOURCE_TIER_ORDER.indexOf(inferredSourceTier)
      ? inferredSourceTier
      : claimedSourceTier;
    const effectiveSourceTier = clampSourceTier(item.kind, provenanceBound);
    if (effectiveSourceTier !== claimedSourceTier) {
      diagnostics.push({ code: "source_tier_clamped", ref: item.evidence_id, detail: `${item.kind} source tier ${claimedSourceTier} clamped to ${effectiveSourceTier}` });
    }
    const sample = assessSampleAdequacy(item);
    const allowedDimensions = new Set(KIND_ALLOWED_DIMENSIONS[item.kind] ?? []);
    const requestedDimensions = item.valid_for_dimensions ?? [];
    const effectiveDimensions = requestedDimensions.filter((dimension) => allowedDimensions.has(dimension));
    for (const dimension of requestedDimensions.filter((dimension) => !allowedDimensions.has(dimension))) {
      diagnostics.push({ code: "evidence_kind_dimension_mismatch", ref: item.evidence_id, detail: `${item.kind} cannot establish ${dimension}` });
    }
    const contentHash = ingestedContentHash(item, { tier: effectiveTier, source_tier: effectiveSourceTier, dimensions: effectiveDimensions });

    records.push({
      evidence_id: item.evidence_id,
      kind: item.kind,
      source: item.source,
      reliability_level: timestampValid && versionMatches ? effectiveTier : "E0",
      source_tier: effectiveSourceTier,
      timestamp: timestampValid ? item.timestamp : collected_at,
      sample_size: item.sample_size ?? null,
      requested_valid_for_dimensions: Array.isArray(item.valid_for_dimensions) ? [...item.valid_for_dimensions] : null,
      sample_adequacy: sample.sample_adequacy,
      recommended_min: sample.recommended_min,
      expiry,
      observation: item.observation,
      applies_to_segment: item.applies_to_segment ?? null,
      supporting_claims: item.supporting_claims ?? [],
      contradicting_claims: item.contradicts_claims ?? [],
      applicability: {
        product_version: item.applies_to_product_version ?? "unknown",
        scope: "existing_user_evidence",
        environment: null,
        persona_ids: item.applies_to_persona_ids ?? [],
        segment: item.applies_to_segment ?? null,
        valid_for_dimensions: effectiveDimensions,
      },
      fact_type: isRealUserTier(effectiveTier) && timestampValid && versionMatches ? "fact" : "inference",
      origin: "caller_supplied",
      version_stable: item.version_stable === true,
      stable_reason: stableAllowed ? item.stable_reason : null,
      integrity_valid: timestampValid && versionMatches,
      product_version_valid: versionMatches,
      semantic_valid: true,
      content_hash: contentHash,
    });
  }
  return { records, expiryUnknown, downgraded, diagnostics };
}

function inferSourceTier(item) {
  if (item?.kind === "team_statement") return "tier_3";
  if (["review", "public_comment"].includes(item?.kind)) return item?.source ? "tier_3" : "untraceable";
  if (["interview", "survey", "usability_test", "usage_data", "retention_data", "payment_record", "contract"].includes(item?.kind)) {
    const source = String(item?.source ?? "").trim();
    const traceable = /^(?:https?:\/\/|archive:\/\/|run:\/\/|tool:\/\/|evidence:\/\/|EV-[A-Za-z0-9._-]+$)/u.test(source);
    return traceable && item?.timestamp ? "tier_1" : "untraceable";
  }
  return "untraceable";
}

/** supporting_claims and explicit dimension lists can only narrow applicability. */
export function mapClaimApplicability(records, hypotheses) {
  const diagnostics = [];
  const normalizedHypotheses = (hypotheses ?? []).map((claim) => {
    const allowed = new Set(CLAIM_TYPE_ALLOWED_DIMENSIONS[claim.claim_type] ?? []);
    const original = claim.affected_dimensions ?? [];
    const affected = original.filter((dimension) => allowed.has(dimension));
    for (const dimension of original.filter((value) => !allowed.has(value))) {
      diagnostics.push({ code: "claim_type_dimension_mismatch", ref: claim.hypothesis_id, detail: `${claim.claim_type} cannot affect ${dimension}` });
    }
    return { ...claim, affected_dimensions: affected };
  });
  const byClaim = new Map(normalizedHypotheses.map((h) => [h.hypothesis_id, h]));
  const mapped = (records ?? []).map((record) => {
    const allowedClaimTypes = new Set(KIND_ALLOWED_CLAIM_TYPES[record.kind] ?? []);
    const filterClaims = (ids, relation) => ids.filter((claimId) => {
      const claim = byClaim.get(claimId);
      if (!claim) {
        diagnostics.push({ code: "unknown_claim_relation", ref: record.evidence_id, detail: claimId });
        return false;
      }
      if (!claim.claim_type || allowedClaimTypes.has(claim.claim_type)) return true;
      diagnostics.push({ code: "evidence_kind_claim_mismatch", ref: record.evidence_id, detail: `${record.kind} cannot ${relation} ${claimId}:${claim.claim_type}` });
      return false;
    });
    const supporting = filterClaims(record.supporting_claims ?? [], "support");
    const contradicting = filterClaims(record.contradicting_claims ?? [], "contradict");
    const claims = [...new Set([...supporting, ...contradicting])];
    const claimDimensions = new Set();
    for (const claimId of claims) {
      const claim = byClaim.get(claimId);
      for (const key of claim.affected_dimensions ?? []) claimDimensions.add(key);
    }
    const kindAllowed = new Set(KIND_ALLOWED_DIMENSIONS[record.kind] ?? []);
    const requested = record.applicability?.valid_for_dimensions ?? [];
    const base = Array.isArray(record.requested_valid_for_dimensions) && record.requested_valid_for_dimensions.length > 0 ? requested : [...kindAllowed];
    const valid = claims.length > 0
      ? base.filter((key) => kindAllowed.has(key) && claimDimensions.has(key))
      : base.filter((key) => kindAllowed.has(key));
    return { ...record, supporting_claims: supporting, contradicting_claims: contradicting, semantic_valid: true, applicability: { ...record.applicability, valid_for_dimensions: valid } };
  });
  return { records: mapped, hypotheses: normalizedHypotheses, diagnostics };
}

export function buildEvidenceEffectLedger(hypotheses, evidence) {
  const ledger = [];
  for (const record of evidence ?? []) {
    for (const claim of hypotheses ?? []) {
      const supports = (record.supporting_claims ?? []).includes(claim.hypothesis_id) ||
        (claim.supporting_refs ?? []).includes(record.evidence_id);
      const contradicts = (record.contradicting_claims ?? []).includes(claim.hypothesis_id) ||
        (claim.contradicting_refs ?? []).includes(record.evidence_id);
      const overlaps = (record.applicability?.valid_for_dimensions ?? []).some((dimension) => (claim.affected_dimensions ?? []).includes(dimension));
      ledger.push({
        evidence_id: record.evidence_id,
        claim_id: claim.hypothesis_id,
        relation: contradicts ? "contradict" : supports ? "support" : overlaps ? "neutral" : "not_applicable",
        effective_tier: record.reliability_level ?? "E0",
        scope_valid: record.scope_valid !== false,
        product_version_valid: record.product_version_valid !== false && record.integrity_valid !== false,
        semantic_valid: record.semantic_valid !== false,
      });
    }
  }
  return ledger;
}

export function deriveClaimEvidenceState(hypotheses, ledger, evidence) {
  const byEvidence = new Map((evidence ?? []).map((record) => [record.evidence_id, record]));
  return (hypotheses ?? []).map((claim) => {
    const effects = (ledger ?? []).filter((entry) => entry.claim_id === claim.hypothesis_id && entry.scope_valid && entry.product_version_valid && entry.semantic_valid);
    const support = effects.filter((entry) => entry.relation === "support");
    const contradict = effects.filter((entry) => entry.relation === "contradict");
    const related = [...support, ...contradict];
    const isSufficient = (entry) => {
      if (!isRealUserTier(entry.effective_tier)) return false;
      const record = byEvidence.get(entry.evidence_id);
      return !["interview", "survey", "usability_test"].includes(record?.kind) || record?.sample_adequacy === "adequate";
    };
    const realSupport = support.filter(isSufficient);
    const realContradict = contradict.filter(isSufficient);
    const authoritativeRelated = related.filter((entry) => !isRealUserTier(entry.effective_tier) || isSufficient(entry));
    const effectiveTier = maxTier(authoritativeRelated.map((entry) => ({ reliability_level: entry.effective_tier })));
    let status = "open";
    if (realSupport.length > 0 && realContradict.length > 0) status = "partially_validated";
    else if (realContradict.length > 0) status = "falsified";
    else if (realSupport.length > 0) status = "validated";
    const supportingRefs = support.map((entry) => entry.evidence_id).filter((id) => byEvidence.has(id));
    const contradictingRefs = contradict.map((entry) => entry.evidence_id).filter((id) => byEvidence.has(id));
    return {
      ...claim,
      current_evidence_level: effectiveTier,
      fact_type: ["validated", "falsified"].includes(status) ? "fact" : related.length > 0 ? "inference" : "assumption",
      status,
      supporting_refs: [...new Set(supportingRefs)],
      contradicting_refs: [...new Set(contradictingRefs)],
      transition_driver: realContradict.length > 0
        ? `canonical E3+ evidence contradicts ${claim.hypothesis_id}`
        : realSupport.length > 0
          ? `canonical E3+ evidence supports ${claim.hypothesis_id}`
          : related.some((entry) => isRealUserTier(entry.effective_tier))
            ? "real evidence is underpowered or has unknown sample adequacy; authoritative status remains open"
            : "simulation evidence only; authoritative status remains open",
    };
  });
}

/** Highest tier across any mix of issued cards and ingested records. */
export function maxTier(entries) {
  return (entries ?? []).reduce(
    (best, entry) =>
      tierOrdinal(entry?.reliability_level) > tierOrdinal(best) ? entry.reliability_level : best,
    "E0",
  );
}

export function hasRealUserEvidence(entries) {
  return (entries ?? []).some((entry) => isRealUserTier(entry?.reliability_level));
}

export function tierDistribution(entries) {
  const distribution = { E0: 0, E1: 0, E2: 0, E3: 0, E4: 0, E5: 0 };
  for (const entry of entries ?? []) {
    if (Object.prototype.hasOwnProperty.call(distribution, entry?.reliability_level)) {
      distribution[entry.reliability_level] += 1;
    }
  }
  return distribution;
}
