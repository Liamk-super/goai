import assert from "node:assert/strict";
import test from "node:test";
import { AUDIT_LABELS_ZH, buildEvidenceSpecialistReportV2, selectEvidenceSpecialistReportV2 } from "../src/specialist-report.mjs";

const identity = {report_id: "30000000-0000-4000-8000-000000000001", run_id: "30000000-0000-4000-8000-000000000002", project_id: "30000000-0000-4000-8000-000000000003", product_version_id: "30000000-0000-4000-8000-000000000004", product_title: "真实产品"};
const source = {source_locator_id: "30000000-0000-4000-8000-000000000005", evidence_id: "30000000-0000-4000-8000-000000000006", source_kind: "PUBLIC_URL", canonical_url: "https://example.com/evidence", title: "证据来源", publisher: "示例机构", published_at: "2026-08-01T00:00:00Z", fetched_at: "2026-08-13T00:00:00Z", locator: {section: "result"}, region: "CN", independence_group: "example", content_sha256: "d".repeat(64)};

test("audit report keeps raw codes in details and exposes Chinese default labels", () => {
  const auditResult = {structured_output: {claims: [{claim_id: "claim-source", text: "证据支持核心判断", criticality: "critical"}], calibration_decisions: [{claim_id: "claim-source", calibrated_text: "证据支持核心判断", citation_status: "VERIFIED", score_bearing: true, evidence_ids: [source.evidence_id], source_locator_ids: [source.source_locator_id]}], conflicts: [], evidence_gaps: []}};
  const report = buildEvidenceSpecialistReportV2({identity, auditResult, sourceDirectory: [source]});
  assert.equal(AUDIT_LABELS_ZH.PENDING_VALIDATION, "待补充证据");
  assert.equal(report.domain_payload.labels.REJECTED, "当前证据不支持");
  assert.equal(selectEvidenceSpecialistReportV2(report, "summary").source_sha256, selectEvidenceSpecialistReportV2(report, "full").source_sha256);
  assert.equal(JSON.stringify(report).includes("iframe"), false);
});
