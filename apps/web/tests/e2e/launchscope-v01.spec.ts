import { expect, test } from "@playwright/test";

const workspaceUrl = process.env.LAUNCHSCOPE_WEB_E2E_URL;

test("workspace renders only API-backed project and run state", async ({ page }) => {
  test.skip(!workspaceUrl, "LAUNCHSCOPE_WEB_E2E_URL and an authenticated test session are required for browser E2E.");
  await page.goto(`${workspaceUrl}/projects`);
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await expect(page.getByText("durable PostgreSQL projections")).toBeVisible();
});
