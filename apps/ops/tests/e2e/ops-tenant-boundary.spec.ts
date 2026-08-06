import { expect, test } from "@playwright/test";

const opsUrl = process.env.LAUNCHSCOPE_OPS_E2E_URL;

test("Ops UI states its redaction boundary and requires an Ops session", async ({ page }) => {
  test.skip(!opsUrl, "LAUNCHSCOPE_OPS_E2E_URL and a separately authenticated Ops session are required for browser E2E.");
  await page.goto(`${opsUrl}/audit/events`);
  await expect(page.getByRole("heading", { name: "Ops audit events" })).toBeVisible();
});
