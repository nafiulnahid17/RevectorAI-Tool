/* Regression: attachment preview must render without separate assets or an API. */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox"],
  });
  try {
    const html = await fs.readFile("app/web/index.html", "utf8");
    const restricted = await browser.newPage({ javaScriptEnabled: false });
    await restricted.setContent(html);
    await restricted.getByRole("heading", { name: "Input artwork", exact: true }).waitFor();
    assert.equal(await restricted.locator(".topbar").evaluate((el) => getComputedStyle(el).display), "flex");
    await restricted.close();
    const errors = [];
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1050 },
    });
    page.on("pageerror", (e) => errors.push(e.message));
    const requests = [];
    page.on("request", (r) => requests.push(r.url()));
    // Opaque-origin attachment preview; this managed browser blocks file:// URLs.
    await page.setContent(html);
    await page
      .getByRole("heading", { name: "Input artwork", exact: true })
      .waitFor();
    await page
      .getByText("Workspace preview · Engine not connected", { exact: true })
      .waitFor();
    assert.equal(
      await page
        .getByRole("button", { name: "Choose file", exact: true })
        .isEnabled(),
      false,
    );
    assert.equal(
      await page
        .locator(".topbar")
        .evaluate((el) => getComputedStyle(el).display),
      "flex",
    );
    assert.ok(
      !requests.some((u) => /\.(css|js)(\?|$)/.test(u)),
      "Preview must not need external CSS or modules",
    );
    await page
      .getByRole("button", { name: "+ Add part size", exact: true })
      .click();
    await page.locator('[name="req-name"]').fill("Front body");
    assert.equal(
      await page.locator('[name="req-name"]').inputValue(),
      "Front body",
    );
    await page.screenshot({
      path: "samples/web-workspace/06-standalone-preview.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    await page.setViewportSize({ width: 1440, height: 1050 });

    // On a preview HTTP host with a hung engine, the workspace appears immediately;
    // connection attempts time out, then a retry can recover when the server is ready.
    let healthReady = false;
    await page.route("https://preview.revector.test/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/")
        return route.fulfill({ contentType: "text/html", body: html });
      if (url.pathname === "/health") {
        if (!healthReady) return; // deliberately no response: AbortController must terminate it
        return route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({ status: "ok", engine: "ReVector" }),
        });
      }
      return route.abort();
    });
    await page.goto("https://preview.revector.test/", {
      waitUntil: "domcontentloaded",
    });
    await page
      .getByRole("heading", { name: "Input artwork", exact: true })
      .waitFor({ timeout: 1500 });
    await page
      .getByText("Workspace preview · Engine not connected", { exact: true })
      .waitFor({ timeout: 8000 });
    healthReady = true;
    await page
      .getByRole("button", { name: "Retry connection", exact: true })
      .click();
    await page.getByText("Engine connected", { exact: true }).waitFor();
    assert.equal(
      await page
        .getByRole("button", { name: "Choose file", exact: true })
        .isEnabled(),
      true,
    );
    await page.addInitScript(() => {
      Object.defineProperty(window, "localStorage", {
        get() {
          throw new DOMException(
            "Storage disabled in preview",
            "SecurityError",
          );
        },
      });
    });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByText("Engine connected", { exact: true }).waitFor();
    assert.equal(
      await page
        .getByRole("button", { name: "Choose file", exact: true })
        .isEnabled(),
      true,
    );
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({
        standalone_preview: "PASS",
        scripts_blocked_layout: "PASS",
        stalled_health_timeout: "PASS",
        retry_connection: "PASS",
        blocked_storage: "PASS",
        mobile_overflow: false,
        page_errors: errors,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
