/**
 * Knowledge retrieval adapter. V0.1 is an INTERFACE with a static KB-ID index.
 *
 * Same shape as product-technical-audit/src/knowledge.mjs so both skills can be
 * bound to one retriever later. The knowledge base is NOT copied here — this
 * module only records which KB IDs govern which concern, so retrieval stays
 * traceable. An unbound retriever returns "retriever_unavailable" and an empty
 * passage list; it never invents knowledge-base content.
 */

export const KB_DOCUMENT = "用户共创Agent 用户研究知识库与行为决策逻辑 V1.0";

/** Which KB IDs govern which concern. Mirrors knowledge/README.md. */
export const KB_INDEX = Object.freeze({
  role: ["KB-USR-R01", "KB-USR-R02", "KB-USR-R03", "KB-USR-R04"],
  target_user_admission: ["KB-USR-R04", "KB-USR-S1", "KB-USR-P02"],
  persona_modeling: ["KB-USR-F01", "KB-USR-G01", "KB-USR-G02", "KB-USR-G03", "KB-USR-S1"],
  jobs_to_be_done: ["KB-USR-F02", "KB-USR-S2"],
  scenario_and_alternatives: ["KB-USR-F02", "KB-USR-F03", "KB-USR-F04", "KB-USR-S2"],
  first_experience: ["KB-USR-F03", "KB-USR-F06", "KB-USR-S3"],
  task_test: ["KB-USR-F06", "KB-USR-F04", "KB-USR-S4"],
  interview_simulation: ["KB-USR-F07", "KB-USR-F05", "KB-USR-S5"],
  insight_extraction: ["KB-USR-F05", "KB-USR-B02"],
  simulation_behavior: ["KB-USR-B01", "KB-USR-B02", "KB-USR-B03", "KB-USR-B04"],
  hypothesis_and_priority: ["KB-USR-S5", "KB-USR-S6", "KB-USR-P01"],
  validation_design: ["KB-USR-V01", "KB-USR-V02", "KB-USR-V03", "KB-USR-V04", "KB-USR-S6"],
  scoring: ["KB-USR-VS01", "KB-USR-VS02", "KB-USR-VS03"],
  guardrails: ["KB-USR-P01", "KB-USR-P02", "KB-USR-P03"],
  output_template: ["KB-USR-T01"],
});

export function kbIdsFor(concern) {
  return KB_INDEX[concern] ?? [];
}

let boundRetriever = null;

export function bindRetriever(retriever) {
  boundRetriever = retriever;
}

export function isRetrieverBound() {
  return boundRetriever !== null;
}

/**
 * @param {string} concern key of KB_INDEX
 * @param {string} [query] optional semantic query, applied after KB-ID filtering
 */
export async function retrieve(concern, query) {
  const kb_ids = kbIdsFor(concern);
  if (!boundRetriever) {
    return {
      status: "retriever_unavailable",
      concern,
      kb_ids,
      passages: [],
      detail:
        "No RAG retriever bound at V0.1. KB IDs are returned for traceability; passage text is not fabricated.",
    };
  }
  return boundRetriever({ concern, kb_ids, query, document: KB_DOCUMENT });
}
