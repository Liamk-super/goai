const { chromium } = require("playwright");
const fs = require("node:fs");

const artifact = "artifacts/acceptance/interactive-20260806-012835";
const fixtureV1 = "D:/programming/gitee/project/goai/tests/e2e/fixtures/v1/product-materials/brief.md";
const fixtureV2 = "D:/programming/gitee/project/goai/tests/e2e/fixtures/v2/product-materials/brief.md";
let activeBrowser;

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  });
  activeBrowser = browser;
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const evidence = {
    started_at: new Date().toISOString(),
    api_responses: [],
    failed_requests: [],
    http_errors: [],
    console_errors: [],
    checks: [],
  };

  page.on("console", (message) => {
    if (message.type() === "error") evidence.console_errors.push(message.text());
  });
  page.on("requestfailed", (request) => {
    evidence.failed_requests.push({
      method: request.method(),
      url: request.url().replace(/\?.*$/, ""),
      error: request.failure()?.errorText,
    });
  });
  page.on("response", (response) => {
    const item = {
      method: response.request().method(),
      status: response.status(),
      url: response.url().replace(/\?.*$/, ""),
    };
    if (response.url().includes("/api/") || response.url().includes(":59000/")) {
      evidence.api_responses.push(item);
    }
    if (response.status() >= 400) evidence.http_errors.push(item);
  });

  const check = (name, condition, details = {}) => {
    evidence.checks.push({ name, passed: Boolean(condition), ...details });
    if (!condition) throw new Error(`Check failed: ${name}`);
  };

  await page.goto("http://127.0.0.1:3000/projects", { waitUntil: "networkidle" });
  check("projects page renders", await page.getByRole("heading", { name: "Projects in motion." }).isVisible());
  await page.getByRole("link", { name: "Open new signal" }).click();
  await page.waitForURL("**/projects/new");
  await page.getByPlaceholder("e.g. Merchant onboarding engine").fill(`Browser demo ${Date.now()}`);
  await page.getByRole("button", { name: "Create durable dossier" }).click();
  await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/, { timeout: 15000 });
  const projectId = page.url().split("/").pop();
  check("project creation navigates to durable URL", /^[0-9a-f-]{36}$/.test(projectId));
  check("project detail renders", await page.getByRole("heading", { name: "A thesis under pressure." }).isVisible());
  await page.screenshot({ path: `${artifact}/10-project-created.png`, fullPage: true });
  process.stdout.write("STEP project-created\n");

  async function completeEvaluation({ label, uploadPath, fixtureLabel, screenshotPrefix }) {
    await page.getByRole("link", { name: "New evaluation" }).click();
    await page.waitForURL("**/new-evaluation");
    await page.getByPlaceholder("V1").fill(label);
    await page.locator('input[type="file"]').setInputFiles(uploadPath);
    await page.getByRole("button", { name: "Upload + diagnose gaps" }).click();
    await page.getByRole("heading", { name: "Close the critical gaps." }).waitFor({ timeout: 20000 });
    const textareas = page.locator("textarea");
    const count = await textareas.count();
    check(`${label} gap questions loaded`, count >= 5, { count });
    for (let index = 0; index < count; index += 1) {
      await textareas.nth(index).fill(`${label} verified browser answer ${index + 1}`);
    }
    await page.screenshot({ path: `${artifact}/${screenshotPrefix}-gaps.png`, fullPage: true });
    await page.getByRole("button", { name: "Confirm profile + plan run" }).click();
    await page.getByRole("heading", { name: "The Run is planned." }).waitFor({ timeout: 20000 });
    const timelineLink = page.getByRole("link", { name: "Open command timeline" });
    const runId = (await timelineLink.getAttribute("href")).split("/").pop();
    check(`${label} run planned`, /^[0-9a-f-]{36}$/.test(runId));
    await timelineLink.click();
    await page.waitForURL(/\/runs\/[0-9a-f-]{36}$/);
    await page.getByRole("heading", { name: "Freeze and execute." }).waitFor({ timeout: 15000 });
    if (fixtureLabel) await page.getByLabel("Fixture version").selectOption({ label: fixtureLabel });
    await page.getByRole("button", { name: "Run local read-only evaluation" }).click();
    await page.getByRole("heading", { name: "Decision chain is ready." }).waitFor({ timeout: 30000 });
    await page.waitForTimeout(1000);
    const eventMetric = await page.locator(".metric").filter({ hasText: "Events received" }).innerText();
    const eventCount = Number(eventMetric.match(/\d+/)?.[0] ?? 0);
    check(`${label} SSE delivered durable events`, eventCount > 0, { eventCount });
    check(`${label} completed`, await page.getByText("COMPLETED", { exact: true }).first().isVisible());
    await page.screenshot({ path: `${artifact}/${screenshotPrefix}-timeline.png`, fullPage: true });
    process.stdout.write(`STEP ${label}-timeline\n`);
    await page.getByRole("link", { name: "Read evidence report" }).click();
    await page.waitForURL(/\/reports\/[0-9a-f-]{36}$/);
    await page.getByRole("heading", { name: "A verdict with receipts." }).waitFor({ timeout: 15000 });
    await page.locator('[aria-label="Evidence chain"]').waitFor({ timeout: 15000 });
    const evidenceItems = page.locator('[aria-label="Evidence chain"] > li');
    const evidenceCount = await evidenceItems.count();
    check(`${label} report contains evidence chain`, evidenceCount > 0, { evidenceCount });
    await evidenceItems.first().locator("summary").click();
    const [popup] = await Promise.all([
      context.waitForEvent("page", { timeout: 10000 }),
      evidenceItems.first().getByRole("button", { name: "Open signed evidence" }).click(),
    ]);
    await popup.waitForLoadState("domcontentloaded", { timeout: 10000 });
    check(`${label} signed evidence read succeeds`, !popup.url().includes("error"));
    await popup.close();
    await page.screenshot({ path: `${artifact}/${screenshotPrefix}-report.png`, fullPage: true });
    process.stdout.write(`STEP ${label}-report\n`);
    await page.goto(`http://127.0.0.1:3000/projects/${projectId}`, { waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Durable evaluations" }).waitFor();
    return runId;
  }

  const runV1 = await completeEvaluation({
    label: "V1",
    uploadPath: fixtureV1,
    fixtureLabel: "V1 baseline",
    screenshotPrefix: "20-v1",
  });
  const runV2 = await completeEvaluation({
    label: "V2",
    uploadPath: fixtureV2,
    fixtureLabel: "V2 remediation",
    screenshotPrefix: "30-v2",
  });

  await page.getByRole("link", { name: "Compare latest to prior" }).click();
  await page.getByRole("heading", { name: "Same standard. New truth." }).waitFor({ timeout: 15000 });
  await page.getByText("COMPARABLE", { exact: true }).waitFor({ timeout: 15000 });
  check("version comparison is comparable", await page.getByText("COMPARABLE", { exact: true }).isVisible());
  await page.screenshot({ path: `${artifact}/40-version-compare.png`, fullPage: true });
  process.stdout.write("STEP compare\n");

  await page.goto("http://127.0.0.1:3001/audit/events", { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: "Audit pulse." }).waitFor();
  const rows = page.locator("tbody tr");
  const rowCount = await rows.count();
  check("Ops redacted ledger has events", rowCount > 0, { rowCount });
  const opsText = await page.locator("body").innerText();
  check("Ops projection does not expose material bodies", !opsText.includes("verified browser answer"));
  await page.screenshot({ path: `${artifact}/50-ops-ledger.png`, fullPage: true });
  await rows.first().getByRole("link").click();
  await page.waitForURL(/\/audit\/runs\/[0-9a-f-]{36}$/);
  await page.screenshot({ path: `${artifact}/51-ops-run.png`, fullPage: true });
  process.stdout.write("STEP ops\n");

  await page.goto(`http://127.0.0.1:3000/runs/00000000-0000-0000-0000-000000000000`, { waitUntil: "domcontentloaded" });
  const alert = page.getByText("run was not found", { exact: true });
  await alert.waitFor({ timeout: 15000 });
  check("unknown run fails visibly", await alert.isVisible(), { message: await alert.innerText() });
  await page.screenshot({ path: `${artifact}/60-error-boundary.png`, fullPage: true });

  const mobile = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  const mobilePage = await mobile.newPage();
  await mobilePage.goto("http://127.0.0.1:3000/projects", { waitUntil: "networkidle" });
  const mobileFit = await mobilePage.evaluate(() => ({
    width: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    canScrollX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  }));
  check("mobile project page has no horizontal overflow", !mobileFit.canScrollX, mobileFit);
  check("mobile primary action remains visible", await mobilePage.getByRole("link", { name: "Open new signal" }).isVisible());
  await mobilePage.screenshot({ path: `${artifact}/70-mobile-projects.png`, fullPage: true });
  await mobile.close();
  process.stdout.write("STEP mobile\n");

  evidence.project_id = projectId;
  evidence.run_v1 = runV1;
  evidence.run_v2 = runV2;
  evidence.finished_at = new Date().toISOString();
  evidence.console_errors = [...new Set(evidence.console_errors)];
  evidence.http_errors = evidence.http_errors.filter((item) => !item.url.endsWith("/favicon.ico"));
  fs.writeFileSync(`${artifact}/browser-flow-evidence.json`, `${JSON.stringify(evidence, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({
    projectId,
    runV1,
    runV2,
    checks: evidence.checks,
    apiResponseCount: evidence.api_responses.length,
    failedRequests: evidence.failed_requests,
    httpErrors: evidence.http_errors,
    consoleErrors: evidence.console_errors,
  }, null, 2)}\n`);
  await browser.close();
})().catch(async (error) => {
  console.error(error);
  if (activeBrowser) await activeBrowser.close().catch(() => {});
  process.exitCode = 1;
});
