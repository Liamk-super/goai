const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe" });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    await page.goto("http://127.0.0.1:3000/reports/d19f744a-3475-555f-94a1-55fd0576749f", { waitUntil: "networkidle" });
    const items = page.locator('[aria-label="Evidence chain"] > li');
    const count = await items.count();
    const first = items.first();
    const before = await first.innerText();
    await first.locator("summary").click();
    const button = first.getByRole("button", { name: "Open signed evidence" });
    const buttonVisible = await button.isVisible();
    const [popup] = await Promise.all([
      context.waitForEvent("page", { timeout: 10000 }),
      button.click(),
    ]);
    await popup.waitForLoadState("domcontentloaded");
    process.stdout.write(`${JSON.stringify({ count, before, after: await first.innerText(), buttonVisible, popupStatus: "opened" }, null, 2)}\n`);
    await popup.close();
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
