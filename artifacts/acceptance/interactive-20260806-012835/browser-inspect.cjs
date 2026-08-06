const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const consoleMessages = [];
  const failedRequests = [];
  const apiResponses = [];

  page.on("console", (message) => {
    consoleMessages.push({ type: message.type(), text: message.text() });
  });
  page.on("requestfailed", (request) => {
    failedRequests.push({ url: request.url(), error: request.failure()?.errorText });
  });
  page.on("response", (response) => {
    if (response.url().includes("/api/")) {
      apiResponses.push({
        method: response.request().method(),
        status: response.status(),
        url: response.url().replace(/\?.*$/, ""),
      });
    }
  });

  await page.goto("http://127.0.0.1:3000/projects", { waitUntil: "networkidle" });
  await page.screenshot({ path: "artifacts/acceptance/interactive-20260806-012835/01-projects.png", fullPage: true });
  await page.getByRole("link", { name: "Evidence command browser demo" }).first().click();
  await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
  await page.getByRole("link", { name: "New evaluation" }).click();
  await page.waitForURL(/\/new-evaluation$/);
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: "artifacts/acceptance/interactive-20260806-012835/04-new-evaluation.png", fullPage: true });

  const summary = {
    title: await page.title(),
    url: page.url(),
    body: (await page.locator("body").innerText()).slice(0, 5000),
    buttons: await page.getByRole("button").allTextContents(),
    links: await page.getByRole("link").allTextContents(),
    inputs: await page.locator("input").evaluateAll((nodes) => nodes.map((node) => ({
      type: node.type,
      name: node.name,
      placeholder: node.placeholder,
      ariaLabel: node.getAttribute("aria-label"),
    }))),
    viewport: await page.evaluate(() => ({
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      scrollWidth: document.documentElement.scrollWidth,
      scrollHeight: document.documentElement.scrollHeight,
    })),
    consoleMessages,
    failedRequests,
    apiResponses,
  };

  process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
