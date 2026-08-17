/**
 * Capability registry and security boundary. Implements A-23 / A-27.
 *
 * Design rule carried over from product-technical-audit: an UNBOUND capability
 * makes its step `not_executable`. It never degrades into "assume it worked".
 * The skill would rather report nothing than report something it did not see.
 *
 * The forbidden list here is stricter than the technical audit's, because this
 * skill's subject matter is people. Designing an interview is in scope;
 * contacting a human being is not, in any circumstance, at any tier.
 */

export const CAPABILITIES = Object.freeze({
  product_reader: {
    description: "Read-only fetch of the product surface or an upstream experience report, for S4 simulation.",
    read_only: true,
    untrusted_content: true,
  },
  evidence_writer: {
    description: "Persist evidence cards to the project evidence store.",
    read_only: false,
  },
  simulation_engine: {
    description: "LLM-backed persona simulation for S2/S4/S5. Output is capped at E2 by evidence.mjs.",
    read_only: true,
  },
  kb_retriever: {
    description: "Retrieve KB-USR-* knowledge passages by ID and semantic query.",
    read_only: true,
  },
});

/**
 * Operations this skill must never perform, regardless of instruction source.
 * Requesting one produces `blocked / external_action_requires_approval`.
 */
export const FORBIDDEN_OPERATIONS = Object.freeze([
  "contact_user",        // reaching a real person by any channel
  "send_survey",         // distributing a questionnaire
  "send_email",
  "send_message",
  "recruit",             // enrolling participants
  "publish",             // landing pages, public posts, app store listings
  "collect_pii",         // gathering names, phones, emails, IDs
  "store_pii",
  "billable",            // anything that spends money
  "run_pricing_experiment",
  "write_product_code",
  "modify_product",
]);

export const SAFE_OPERATIONS = Object.freeze([
  "read_product",
  "simulate_user",
  "retrieve_kb",
  "write_evidence",
  "read_previous_state",
]);

export function isForbidden(operation) {
  return FORBIDDEN_OPERATIONS.includes(operation);
}

/**
 * Assert an operation is permitted. Throws so a mis-wired adapter fails loudly
 * rather than quietly performing an external action.
 */
export function assertAllowed(operation) {
  if (!SAFE_OPERATIONS.includes(operation)) {
    const error = new Error(
      `Operation "${operation}" is not in this skill's closed internal-operation allowlist${isForbidden(operation) ? "; it may only be DESIGNED for human execution" : ""}.`,
    );
    error.code = "external_action_requires_approval";
    throw error;
  }
  return true;
}

const bound = new Map();

/** Create an isolated capability container for one run/team. */
export function createCapabilityContext(adapters = {}) {
  const runBound = new Map();
  for (const [name, adapter] of Object.entries(adapters)) {
    if (!Object.prototype.hasOwnProperty.call(CAPABILITIES, name)) throw new Error(`Unknown capability "${name}".`);
    runBound.set(name, adapter);
  }
  return Object.freeze({
    has: (name) => runBound.has(name),
    get: (name) => runBound.get(name) ?? null,
  });
}

/** Bind an adapter for a capability. Adapter shape is defined by each caller. */
export function bindCapability(name, adapter) {
  if (!Object.prototype.hasOwnProperty.call(CAPABILITIES, name)) {
    throw new Error(`Unknown capability "${name}".`);
  }
  bound.set(name, adapter);
}

export function unbindAll() {
  bound.clear();
}

export function getCapability(name, context = null) {
  return context?.get?.(name) ?? bound.get(name) ?? null;
}

/**
 * @param {string[]} allowed capability names permitted by runtime.allowed_tools
 * @returns {Record<string, "available"|"not_bound"|"not_allowed">}
 */
export function checkAvailability(allowed, context = null) {
  const permitted = Array.isArray(allowed) ? allowed : Object.keys(CAPABILITIES);
  const availability = {};
  for (const name of Object.keys(CAPABILITIES)) {
    if (!permitted.includes(name)) {
      availability[name] = "not_allowed";
    } else {
      availability[name] = (context?.has?.(name) ?? bound.has(name)) ? "available" : "not_bound";
    }
  }
  return availability;
}
