import { expect, test } from "@playwright/test";

const workspaceUrl = process.env.LAUNCHSCOPE_WEB_E2E_URL;

test("home and project archive have distinct responsibilities", async ({ page }) => {
  test.skip(!workspaceUrl, "LAUNCHSCOPE_WEB_E2E_URL and an authenticated test session are required for browser E2E.");

  await page.goto(`${workspaceUrl}/`);
  await expect(page.locator(".evaluation-wheel")).toHaveCount(1);

  await page.goto(`${workspaceUrl}/projects`);
  await expect(page.getByRole("heading", { name: "Projects in motion." })).toBeVisible();
  await expect(page.locator(".evaluation-wheel")).toHaveCount(0);

  await page.goto(`${workspaceUrl}/projects/new`);
  await expect(page).toHaveURL(`${workspaceUrl}/?start=1`);
  await expect(page.locator(".dock-panel")).toBeVisible();
});

test("recorded v2.2 report route exposes the canonical report and all four specialist entries", async ({ page }) => {
  test.skip(!workspaceUrl, "LAUNCHSCOPE_WEB_E2E_URL is required for Recorded browser acceptance.");
  const reportId = "45454545-4545-4454-8454-454545454545";
  const runId = "12121212-1212-4212-8212-121212121212";
  await page.route(`**/api/v1/experience/v2/reports/${reportId}`, route => route.fulfill({
    json: {
      report_schema_version: "2.0",
      document: {
        schema_version: "2.0", report_id: reportId, run_id: runId,
        project_id: "23232323-2323-4232-8232-232323232323",
        product_version_id: "34343434-3434-4343-8343-343434343434",
        product_title: "Recorded v2.2", source_sha256: "a".repeat(64),
        top_card: { potential_index: 68, stage: "MVP", confidence_band: "MEDIUM", evidence_coverage: .62, recommendation: "VALIDATE_FURTHER" },
        summary_claim_id: "claim-summary",
        claims: [{ claim_id: "claim-summary", section: "CONCLUSION", text: "Recorded canonical claim", status: "PENDING_VALIDATION", decision_relevance: "CONTEXT", citation_ids: [], score_bearing: false }],
        highlights: ["claim-summary"], critical_issues: [], role_summaries: { user: [], product: [], investment: [] }, cross_domain_claims: [], actions: [],
        confidence_breakdown: { profile_ref: "confidence@2", audited_evidence_quality: .6, evidence_coverage: .62, independent_source_support: .5, freshness: .8, cross_domain_agreement: .7, unresolved_conflict_penalty: 0, score: .64, band: "MEDIUM" },
        agent_report_cards: ["user-evidence", "product-engineering", "business-investment", "evidence-auditor"].map((agent_code, index) => ({ agent_code, report_id: `${index + 1}0000000-1111-4111-8111-111111111111`, title: agent_code, summary_claim_ids: ["claim-summary"], source_sha256: "b".repeat(64) })),
        citations: [], source_directory: [], audit_detail_ref: "evidence-auditor",
      },
      integrity: { canonical_sha256: "c".repeat(64), source_sha256: "a".repeat(64) },
      projection: { view: "FULL", created_at: "2026-08-13T00:00:00Z" },
    },
  }));

  await page.goto(`${workspaceUrl}/reports/${reportId}`);
  await expect(page.getByText("Hit potential index", { exact: true })).toBeVisible();
  await expect(page.locator(".report-v22-agent-reports li")).toHaveCount(4);
  await expect(page.getByText("Compared with last time", { exact: true })).toHaveCount(0);
});
