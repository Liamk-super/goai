const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe" });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const errors = [];
    page.on("response", (response) => {
      if (response.status() >= 400) errors.push({ status: response.status(), url: response.url().replace(/\?.*$/, "") });
    });
    await page.goto("http://127.0.0.1:3000/projects", { waitUntil: "networkidle" });
    const webIcon = await page.locator('link[rel="icon"]').getAttribute("href");
    await page.goto("http://127.0.0.1:3001/audit/events", { waitUntil: "networkidle" });
    await page.getByRole("columnheader", { name: "Delivery" }).waitFor();
    const opsIcon = await page.locator('link[rel="icon"]').getAttribute("href");
    await page.screenshot({ path: "artifacts/acceptance/interactive-20260806-012835/80-ops-delivery-label.png", fullPage: true });
    process.stdout.write(`${JSON.stringify({ webIcon, opsIcon, deliveryHeader: true, httpErrors: errors }, null, 2)}\n`);
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
