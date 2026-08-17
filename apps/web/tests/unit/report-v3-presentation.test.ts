import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { specialistPayloadSections, specialistViewFromQuery, visibleReportPriorities } from "../../src/lib/report-v3-presentation.ts";

test("supervisor first screen is bounded to three issues and three actions", () => {
  const result = visibleReportPriorities(
    ["P0", "P1", "P2", "P2"].map((priority, index) => ({ priority, claim_id: `claim-${index}`, decision_impact: "impact" })),
    Array.from({ length: 6 }, (_, index) => ({ action_id: `action-${index}` })),
  );
  assert.equal(result.issues.length, 3);
  assert.equal(result.actions.length, 3);
});

test("each specialist role exposes an independently readable professional structure", () => {
  const roles = [
    { kind: "USER_EVIDENCE", target_segments: ["学生"], validation_plan: ["复验"] },
    { kind: "PRODUCT_ENGINEERING", stage_gate: "DEMO", core_flows: ["完成任务"] },
    { kind: "BUSINESS_INVESTMENT", business_model: ["订阅"], investment_gates: ["续费"] },
    { kind: "EVIDENCE_AUDIT", source_independence: ["两个来源"], evidence_gaps: ["留存"] },
  ];
  for (const role of roles) {
    const sections = specialistPayloadSections(role);
    assert.ok(sections.length >= 2);
    assert.ok(sections.every(section => section.items.length > 0));
  }
});

test("specialist deep links select the requested canonical view", () => {
  assert.equal(specialistViewFromQuery("full"), "full");
  assert.equal(specialistViewFromQuery("summary"), "summary");
  assert.equal(specialistViewFromQuery("unsupported"), "summary");
});

test("the comprehensive conclusion is prose and the full report keeps named sections", () => {
  const source = readFileSync("apps/web/src/components/reports/v3/SupervisorReportV3.tsx", "utf8");
  assert.match(source, /className="report-v3-summary-copy"/);
  assert.doesNotMatch(source, /<h2 id="report-v3-summary">\{summaryClaim/);
  assert.match(source, /renderClaimSection/);
  assert.match(source, /No evidence-backed findings are available in this section yet/);
});

test("v3 reports expose canonical claim and citation identities only in audit details", () => {
  const supervisor = readFileSync("apps/web/src/components/reports/v3/SupervisorReportV3.tsx", "utf8");
  const specialist = readFileSync("apps/web/src/components/reports/v3/SpecialistReportV3.tsx", "utf8");
  for (const source of [supervisor, specialist]) {
    assert.match(source, /data-export-audit="true"/);
    assert.match(source, /claim\.claim_id/);
    assert.match(source, /citations\.get\(citationId\)\?\.label/);
  }
});

test("expandable citations are never nested in paragraph or inline elements", () => {
  const source = readFileSync("apps/web/src/components/reports/v3/SupervisorReportV3.tsx", "utf8");
  assert.doesNotMatch(source, /<p className="report-v3-summary-copy">/);
  assert.doesNotMatch(source, /<p>\{presentReportText\(locale, claim\.text\)\} \{renderCitations\(claim\)\}<\/p>/);
  assert.doesNotMatch(source, /<span>\{presentReportText\(locale, claim\.text\)\} \{renderCitations\(claim\)\}<\/span>/);
});
