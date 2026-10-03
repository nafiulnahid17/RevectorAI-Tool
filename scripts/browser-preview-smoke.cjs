/* Connection indicators reflect real responses; retry is shown only for failures. */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const http = require("node:http");

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox"],
  });
  try {
    const html = await fs.readFile("app/web/index.html", "utf8");
    const restricted = await browser.newPage({ javaScriptEnabled: false });
    await restricted.setContent(html);
    await restricted
      .getByRole("heading", { name: "Input artwork", exact: true })
      .waitFor();
    assert.equal(
      await restricted
        .locator(".topbar")
        .evaluate((el) => getComputedStyle(el).display),
      "flex",
    );
    assert.equal(
      await restricted.getByRole("button", { name: /Retry/ }).count(),
      0,
    );
    await restricted.close();
    const errors = [];
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1050 },
    });
    page.on("pageerror", (e) => errors.push(e.message));
    const requests = [];
    page.on("request", (r) => requests.push(r.url()));
    await page.setContent(html);
    await page.locator('[data-connection="server"].failed').waitFor();
    assert.equal(
      await page.locator('[data-connection="engine"].waiting').count(),
      1,
    );
    assert.equal(
      await page.locator('[data-connection="tool"].waiting').count(),
      1,
    );
    assert.equal(
      await page
        .getByRole("button", { name: "Choose file", exact: true })
        .isEnabled(),
      false,
    );
    assert.ok(!requests.some((u) => /\.(css|js)(\?|$)/.test(u)));
    assert.equal(await page.locator(".connection-notice").count(), 0);
    await page
      .getByRole("button", { name: "+ Add part size", exact: true })
      .click();
    await page.locator('[name="req-name"]').fill("Front body");
    assert.equal(
      await page.locator('[name="req-name"]').inputValue(),
      "Front body",
    );
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    await page.setViewportSize({ width: 1440, height: 1050 });

    let serverReady = false,
      engineReady = false,
      toolReady = false;
    await page.route("https://preview.revector.test/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/")
        return route.fulfill({ contentType: "text/html", body: html });
      if (url.pathname === "/health") {
        if (!serverReady) return;
        return route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({ status: "ok", engine: "ReVector" }),
        });
      }
      if (url.pathname === "/health/ready") {
        return route.fulfill({
          status: engineReady && toolReady ? 200 : 503,
          contentType: "application/json",
          body: JSON.stringify({
            status: engineReady && toolReady ? "ready" : "not_ready",
            engine: "ReVector",
            dependencies: { inkscape: true },
            segments: {
              server: { status: "connected" },
              engine: { status: engineReady ? "connected" : "failed" },
              tool: {
                status: engineReady && toolReady ? "connected" : "failed",
              },
            },
          }),
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
    assert.equal(await page.getByRole("button", { name: /Retry/ }).count(), 0);
    await page
      .locator('[data-connection="server"].failed')
      .waitFor({ timeout: 8000 });
    serverReady = true;
    await page
      .getByRole("button", { name: "Retry server connection", exact: true })
      .click();
    await page.locator('[data-connection="engine"].failed').waitFor();
    assert.equal(
      await page.locator('[data-connection="server"].connected').count(),
      1,
    );
    assert.equal(
      await page
        .getByRole("button", { name: "Retry server connection", exact: true })
        .count(),
      0,
    );
    engineReady = true;
    await page
      .getByRole("button", { name: "Retry engine connection", exact: true })
      .click();
    await page.locator('[data-connection="tool"].failed').waitFor();
    assert.equal(
      await page.locator('[data-connection="engine"].connected').count(),
      1,
    );
    toolReady = true;
    await page
      .getByRole("button", { name: "Retry tool connection", exact: true })
      .click();
    await page.getByText("Ready", { exact: true }).waitFor();
    assert.equal(
      await page.locator(".connection-segment.connected").count(),
      3,
    );
    assert.equal(await page.getByRole("button", { name: /Retry/ }).count(), 0);
    assert.equal(
      await page
        .getByRole("button", { name: "Choose file", exact: true })
        .isEnabled(),
      true,
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
    await page.addInitScript(() => {
      Object.defineProperty(window, "localStorage", {
        get() {
          throw new DOMException("Storage disabled", "SecurityError");
        },
      });
    });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByText("Ready", { exact: true }).waitFor();
    // A server can send headers and then stall midway through its JSON body.
    // The five-second limit must include decoding, not just receiving headers.
    const stalled = http.createServer((req, res) => {
      if (req.url === "/") {
        res.writeHead(200, { "Content-Type": "text/html" });
        res.end(html);
      } else if (req.url === "/health") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.write('{"status":');
      } else {
        res.writeHead(404);
        res.end();
      }
    });
    await new Promise((resolve) => stalled.listen(0, "127.0.0.1", resolve));
    try {
      await page.goto(`http://127.0.0.1:${stalled.address().port}/`, {
        waitUntil: "domcontentloaded",
      });
      await page
        .locator('[data-connection="server"].failed')
        .waitFor({ timeout: 8000 });
    } finally {
      stalled.closeAllConnections();
      await new Promise((resolve) => stalled.close(resolve));
    }
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({
        indicators: "PASS",
        retry_only_on_failure: "PASS",
        stalled_health_timeout: "PASS",
        stalled_body_timeout: "PASS",
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
