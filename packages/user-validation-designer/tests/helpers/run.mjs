/**
 * Shared test helpers: load an example input and run the skill with the
 * reference executor bound. Contract tests and the example generator use
 * exactly the same call path, so a committed example can never disagree with
 * what the tests assert.
 */
import { readFileSync } from "node:fs";
import { runValidationDesign } from "../../src/index.mjs";
import { createCapabilityContext, unbindAll } from "../../src/tools/index.mjs";
import { createReferenceExecutor, REFERENCE_TIMESTAMP } from "../fixtures/reference-executor.mjs";
import { computeRegressionBaselineHash } from "../../src/state-integrity.mjs";

const EXAMPLES = new URL("../../examples/", import.meta.url);

export function loadExample(name) {
  return JSON.parse(readFileSync(new URL(name, EXAMPLES), "utf8"));
}

export function refreshRegressionHash(input) {
  input.previous_validation_results_hash = computeRegressionBaselineHash(input.previous_validation_results);
  return input;
}

/**
 * Bind the capabilities a simulation run needs. The adapters are inert: the
 * reference executor supplies the step outcomes, so these only flip
 * availability from "not_bound" to "available".
 */
function simulationCapabilityContext(names = ["simulation_engine", "product_reader", "evidence_writer"]) {
  return createCapabilityContext(Object.fromEntries(names.map((name) => [name, { kind: "test-fixture" }])));
}

/** Run with simulation capabilities bound — the happy path. */
export async function runBound(input, opts = {}) {
  unbindAll();
  const capabilityContext = simulationCapabilityContext(opts.capabilities);
  try {
    return await runValidationDesign(input, {
      executeStep: createReferenceExecutor(opts),
      now: REFERENCE_TIMESTAMP,
      capabilityContext,
    });
  } finally {
    unbindAll();
  }
}

/** Run with nothing bound — must block, never fabricate. */
export async function runUnbound(input) {
  unbindAll();
  return runValidationDesign(input, {});
}
