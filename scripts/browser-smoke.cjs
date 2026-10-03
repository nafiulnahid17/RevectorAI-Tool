/* Runs the real API-backed browser workflow, including downloaded file assertions. */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

(async () => {
  const base = process.env.REVECTOR_TEST_URL || "http://127.0.0.1:8000";
  const out = path.resolve("samples/web-workspace");
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1050 },
    acceptDownloads: true,
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("response", (r) => {
    if (r.status() >= 400) console.error("HTTP failure", r.status(), r.url());
  });
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  await page.goto(base);
  await page.getByText("Engine connected", { exact: true }).waitFor();
  await page.screenshot({
    path: path.join(out, "01-input.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Multi-part sample", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Source uploaded", exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "Analyze input", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Detect parts", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "Detect parts", exact: true }).click();
  await page
    .getByRole("heading", { name: "Detected layout", exact: true })
    .waitFor();
  await page.waitForFunction(() => !document.querySelector(".busy-banner"));
  const rows = page.locator(".part-row");
  const count = await rows.count();
  assert.ok(count >= 2, "Multi-panel sample should detect separate components");
  for (let i = 0; i < count; i++) {
    await rows.nth(i).click();
    await page
      .locator('[name="part-name"]')
      .fill(i === 0 ? "Front body" : "Back body");
    await page
      .locator('[name="part-type"]')
      .selectOption(i === 0 ? "front_body" : "back_body");
    await page.locator('[name="part-width"]').fill(i === 0 ? "520" : "540");
    await page
      .getByRole("button", { name: "Save & confirm part", exact: true })
      .click();
    await page.waitForFunction(() => !document.querySelector(".busy-banner"));
  }
  await page.screenshot({
    path: path.join(out, "02-parts.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Vectorize confirmed parts", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Continue to download", exact: true })
    .waitFor({ timeout: 120000 });
  await page.waitForFunction(() => document.querySelector("#vector-art svg"));
  const shape = page.locator("#vector-art .editable-shape").first();
  await shape.click({ force: true });
  await page.locator("#shape-color").fill("#ee3344");
  await page
    .getByRole("button", { name: "Apply color & revalidate", exact: true })
    .click();
  await page.waitForFunction(
    () => !document.querySelector(".busy-banner"),
    {},
    { timeout: 120000 },
  );
  await page.screenshot({
    path: path.join(out, "03-vector-review.png"),
    fullPage: true,
  });
  const pid = await page.evaluate(() =>
    localStorage.getItem("revector.project"),
  );
  const project = await (
    await page.request.get(base + "/api/revector/projects/" + pid)
  ).json();
  assert.equal(project.true_vector_ready, true);
  assert.equal(project.validation.embedded_rasters, 0);
  assert.ok(project.validation.path_count > 0);
  assert.ok(project.manual_changes.some((c) => c.action === "vector_fill"));
  await page
    .getByRole("button", { name: "Continue to download", exact: true })
    .click();
  await page.screenshot({
    path: path.join(out, "04-downloads.png"),
    fullPage: true,
  });
  const dlPromise = page.waitForEvent("download");
  await page.locator('[data-action="download-part"]').first().click();
  const download = await dlPromise;
  await download.saveAs(path.join(out, "individual.svg"));
  const svg = await fs.readFile(path.join(out, "individual.svg"), "utf8");
  assert.ok(svg.includes("mm"));
  assert.ok(!/<(?:\w+:)?image\b|data:image\//i.test(svg));
  await page.waitForFunction(() => !document.querySelector(".busy-banner"));
  await page.locator('[name="export-part"]').nth(1).uncheck();
  const selectedPromise = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Selected parts ZIP", exact: true })
    .click();
  await (await selectedPromise).saveAs(path.join(out, "selected-parts.zip"));
  await page.waitForFunction(() => !document.querySelector(".busy-banner"));
  await page.locator('[data-format="pdf"]').click();
  const pdfPromise = page.waitForEvent("download");
  await page.locator('[data-action="download-part"]').first().click();
  await (await pdfPromise).saveAs(path.join(out, "individual.pdf"));
  await page.waitForFunction(() => !document.querySelector(".busy-banner"));
  const zipPromise = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Download full ZIP", exact: true })
    .first()
    .click();
  await (await zipPromise).saveAs(path.join(out, "production-pack.zip"));
  await page.waitForFunction(() => !document.querySelector(".busy-banner"));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(out, "05-mobile.png"),
    fullPage: true,
  });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
    "Mobile viewport should not overflow horizontally",
  );
  assert.deepEqual(errors, []);
  await fs.writeFile(
    path.join(out, "verification.json"),
    JSON.stringify(
      {
        project_id: pid,
        detected_parts: count,
        validation: project.validation,
        browser_errors: errors,
        downloaded_files: [
          "individual.svg",
          "individual.pdf",
          "selected-parts.zip",
          "production-pack.zip",
        ],
        tested_viewports: ["1440x1050", "390x844"],
      },
      null,
      2,
    ),
  );
  console.log(
    JSON.stringify({
      project_id: pid,
      parts: count,
      paths: project.validation.path_count,
      rasters: project.validation.embedded_rasters,
      browser_errors: errors,
      output: out,
    }),
  );
  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
