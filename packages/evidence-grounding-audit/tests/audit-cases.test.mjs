import assert from "node:assert/strict";
import test from "node:test";
import { assertSupervisorHandoff, runAudit } from "../src/index.mjs";

const NOW = "2026-08-11T00:00:00Z";
function ev(id, overrides = {}) { return { evidence_id: id, source: `tool://${id}`, source_tier: "tier_1", reliability_level: "E3", timestamp: "2026-08-01T00:00:00Z", expiry: "2027-01-01T00:00:00Z", product_version: "v1", observation: id, ...overrides }; }
function claim(id, text, refs = [], overrides = {}) { return { claim_id: id, text, claim_type: "general", evidence_refs: refs, ...overrides }; }
function result(source_agent, claims, evidence, status = "COMPLETED") { return { source_agent, status, project_id: "p", product_version: "v1", payload: { claims, evidence_cards: evidence } }; }
function run(agent_results, semantic_analysis = []) { return runAudit({ task_id: "t", project_id: "p", product_version: "v1", generated_at: NOW, expected_agents: ["product", "user", "investment"], agent_results, semantic_analysis }); }
function decision(output, id) { return output.structured_output.calibration_decisions.find((item) => item.claim_id === id); }

test("CASE 01 critical claim without evidence_refs is REJECT", () => {
  const out = run([result("product", [claim("C1", "Critical unsupported claim", [], { decision_impact: "critical" })], [])]);
  assert.equal(decision(out, "C1").verdict, "REJECT");
});

test("CASE 02 team statement cannot become a verified fact", () => {
  const out = run([result("product", [claim("C2", "Team says the capability is proven", ["E2"])], [ev("E2", { reliability_level: "E0", source_tier: "tier_3" })])]);
  assert.equal(decision(out, "C2").verdict, "REJECT");
});

test("CASE 03 simulated user mislabeled as real is downgraded", () => {
  const out = run([result("user", [claim("C3", "真实用户非常需要该产品", ["E3"], { claim_type: "demand" })], [ev("E3", { reliability_level: "E2", simulated: true, source: "simulation://P1", source_tier: "tier_3" })])]);
  assert.equal(decision(out, "C3").verdict, "DOWNGRADE");
  assert.ok(decision(out, "C3").reason_codes.includes("simulation_mislabeled"));
});

test("CASE 04 three trust claims merge into one cross-agent issue", () => {
  const rs = ["product", "user", "investment"].map((agent, i) => result(agent, [claim(`C4${i}`, i === 0 ? "平台虚假内容侵蚀信任" : i === 1 ? "商业内容令用户难以建立信任" : "虚假种草损伤长期信任", [`E4${i}`], { claim_type: "trust" })], [ev(`E4${i}`)]));
  const out = run(rs);
  const canonical = out.structured_output.canonical_claims.find((item) => item.normalized_topic === "trust_integrity");
  assert.equal(canonical.agent_count, 3);
  assert.equal(out.structured_output.cross_agent_issues.length, 1);
});

test("CASE 05 same underlying source counts once across three agents", () => {
  const rs = ["product", "user", "investment"].map((agent, i) => result(agent, [claim(`C5${i}`, "平台信任风险", [`E5${i}`], { claim_type: "trust" })], [ev(`E5${i}`, { source: `https://mirror${i}.test/a`, content_fingerprint: "same-article" })]));
  const canonical = run(rs).structured_output.canonical_claims.find((item) => item.normalized_topic === "trust_integrity");
  assert.equal(canonical.agent_count, 3);
  assert.equal(canonical.independent_evidence_count, 1);
});

test("CASE 06 incompatible product stages create stage_conflict", () => {
  const out = run([result("product", [claim("C6A", "product stage early_product", ["E6A"], { claim_type: "product_stage", value: "early_product" })], [ev("E6A")]), result("investment", [claim("C6B", "product stage mature_operation", ["E6B"], { claim_type: "product_stage", value: "mature_operation" })], [ev("E6B")])]);
  assert.ok(out.structured_output.conflicts.some((item) => item.conflict_type === "stage_conflict"));
});

test("CASE 07 MAU without aligned definitions is a metric gap, not data conflict", () => {
  const out = run([result("product", [claim("C7A", "MAU 100", ["E7A"], { claim_type: "mau", value: 100 })], [ev("E7A")]), result("investment", [claim("C7B", "MAU 300", ["E7B"], { claim_type: "mau", value: 300 })], [ev("E7B")])]);
  assert.ok(out.structured_output.conflicts.some((item) => item.conflict_type === "metric_definition_gap"));
  assert.ok(!out.structured_output.conflicts.some((item) => item.conflict_type === "data_conflict"));
});

test("CASE 08 same-scope numeric difference creates data_conflict", () => {
  const scope = { claim_type: "mau", metric_definition: "monthly active accounts", time_scope: "2026-07", geography: "CN", population: "registered accounts", method: "warehouse distinct", product_version: "v1" };
  const out = run([result("product", [claim("C8A", "MAU 100", ["E8A"], { ...scope, value: 100 })], [ev("E8A")]), result("investment", [claim("C8B", "MAU 300", ["E8B"], { ...scope, value: 300 })], [ev("E8B")])]);
  assert.ok(out.structured_output.conflicts.some((item) => item.conflict_type === "data_conflict"));
});

test("CASE 09 weak demand evidence and positive investment view create DecisionTension", () => {
  const out = run([result("user", [claim("C9A", "真实用户需求证据偏弱", ["E9A"], { claim_type: "demand" })], [ev("E9A", { reliability_level: "E2", simulated: true })]), result("investment", [claim("C9B", "项目整体仍有中等投资潜力", ["E9B"], { claim_type: "investment_potential" })], [ev("E9B")])]);
  assert.equal(out.structured_output.decision_tensions.length, 1);
  assert.ok(!out.structured_output.conflicts.some((item) => item.topic === "demand_evidence_vs_investment_potential"));
});

test("CASE 10 market growth to certain product success never passes", () => {
  const out = run([result("investment", [claim("C10", "市场增长，因此产品一定成功", ["E10"], { claim_type: "market", decision_impact: "critical" })], [ev("E10")])]);
  assert.notEqual(decision(out, "C10").verdict, "PASS");
  assert.ok(decision(out, "C10").reason_codes.includes("over_inference"));
});

test("CASE 11 third-party estimate presented as official audited revenue is rejected", () => {
  const out = run([result("investment", [claim("C11", "第三方估算收入是官方审计收入", ["E11"], { claim_type: "financial" })], [ev("E11", { source_tier: "tier_2" })])]);
  assert.equal(decision(out, "C11").verdict, "REJECT");
});

test("CASE 12 expired traceable evidence requests refresh", () => {
  const out = run([result("investment", [claim("C12", "Current market claim", ["E12"], { decision_impact: "critical" })], [ev("E12", { expiry: "2026-01-01T00:00:00Z" })])]);
  assert.equal(decision(out, "C12").verdict, "REQUEST_MORE_EVIDENCE");
});

test("CASE 13 syndicated copies are source-independent deduplicated", () => {
  const out = run([result("product", [claim("C13A", "平台信任风险", ["E13A"], { claim_type: "trust" })], [ev("E13A", { source: "https://a.test/x", original_source: "wire://1" })]), result("user", [claim("C13B", "平台信任风险", ["E13B"], { claim_type: "trust" })], [ev("E13B", { source: "https://b.test/x", original_source: "wire://1" })])]);
  assert.equal(out.structured_output.canonical_claims.find((item) => item.normalized_topic === "trust_integrity").independent_evidence_count, 1);
});

test("CASE 14 SupervisorHandoff fails closed if REJECT enters accepted claims", () => {
  assert.throws(() => assertSupervisorHandoff({ accepted_claims: [{ verdict: "REJECT" }] }), /REJECT claim entered/);
});

test("CASE 15 blocked upstream Agent yields a completed partial audit", () => {
  const out = run([result("product", [claim("C15", "Verified product capability", ["E15"])], [ev("E15")]), result("user", [], [], "BLOCKED"), result("investment", [claim("C15I", "Investment option remains", ["E15I"], { claim_type: "investment_potential" })], [ev("E15I")])]);
  assert.equal(out.status, "completed");
  assert.equal(out.structured_output.supervisor_handoff.audit_coverage.partial_audit, true);
  assert.deepEqual(out.structured_output.supervisor_handoff.audit_coverage.missing_agents, ["user"]);
});

test("summary and full reports share the structured output digest", () => {
  const out = run([result("product", [claim("CR", "Verified claim", ["ER"])], [ev("ER")])]);
  const digest = out.structured_output.structured_output_digest;
  assert.match(out.structured_output.reports.summary_html, new RegExp(digest));
  assert.match(out.structured_output.reports.full_html, new RegExp(digest));
});

test("v2.1 public market claims without source locators stay pending and cannot score", () => {
  const out = run([result("investment", [claim("C21", "Current market growth", ["E21"], { claim_type: "market" })], [ev("E21", { source_type: "PUBLIC_RESEARCH", source_locator_ids: [] })])]);
  const audited = decision(out, "C21");
  assert.equal(audited.verdict, "REQUEST_MORE_EVIDENCE");
  assert.equal(audited.citation_status, "PENDING_VALIDATION");
  assert.equal(audited.score_bearing, false);
});

test("v2.1 internal material can support a claim without fabricating a public locator", () => {
  const out = run([result("product", [claim("C22", "The uploaded build log records a successful smoke test", ["E22"])], [ev("E22", { source_type: "MATERIAL", source: "material://build-log", source_locator_ids: [] })])]);
  const audited = decision(out, "C22");
  assert.equal(audited.verdict, "PASS");
  assert.equal(audited.citation_status, "VERIFIED");
  assert.equal(audited.score_bearing, true);
});

test("v2.1 publisher syndications share one independence group", () => {
  const out = run([result("product", [claim("C23", "平台信任风险", ["E23A", "E23B"], { claim_type: "trust" })], [
    ev("E23A", { source: "https://example.test/a", source_locator_ids: ["L23A"], independence_group: "publisher:wire:story-1" }),
    ev("E23B", { source: "https://mirror.test/a", source_locator_ids: ["L23B"], independence_group: "publisher:wire:story-1" }),
  ])]);
  assert.equal(decision(out, "C23").independent_source_count, 1);
});

test("v2.1 expired support lowers freshness and is not score bearing", () => {
  const out = run([result("product", [claim("C24", "Expired capability claim", ["E24"])], [ev("E24", { expiry: "2026-01-01T00:00:00Z" })])]);
  const audited = decision(out, "C24");
  assert.equal(audited.freshness_status, "EXPIRED");
  assert.ok(audited.freshness_score < 1);
  assert.equal(audited.score_bearing, false);
});

test("v2.1 downgrade preserves the source claim and admits only weaker wording", () => {
  const out = run([result("product", [claim("C25", "Capability is proven", ["E25"])], [ev("E25", { reliability_level: "E2", simulated: true })])]);
  const audited = decision(out, "C25");
  assert.equal(audited.verdict, "DOWNGRADE");
  assert.match(audited.calibrated_text, /Capability is proven/u);
  assert.equal(audited.citation_status, "DOWNGRADED");
  assert.equal(audited.score_bearing, true);
});
