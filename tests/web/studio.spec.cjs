/* Browser checks use mocked services: no PDFs or text leave this machine.
   Requires Playwright's chromium browser. Run with: node tests/web/studio.spec.cjs */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");
const web = path.resolve(__dirname, "../../app/web");
const output = path.resolve(__dirname, "../../output/ui-verification");
const health = {
  transliteration_configured: true,
  supported_languages: ["en", "hi", "ta", "ur"],
  available_font_languages: ["en", "hi", "ta", "ur"],
  max_upload_mb: 25,
  max_pages: 100,
  translation_configured: true,
  ocr_configured: true,
};
let browser;
function reviewFixture() {
  const region = (id, page_number, status, issues = []) => ({
    id,
    page_number,
    status,
    issues,
    source_rects: [[45, 100, 550, 145]],
    translated_rects: [[45, 100, 550, 165]],
    source_text: "The project report is due on October 31, 2026.",
    translated_text: "परियोजना रिपोर्ट 31 अक्टूबर, 2026 तक जमा करनी है।",
    rendered: true,
  });
  return {
    document_id: "doc",
    version: "v1",
    filename: "Project report.pdf",
    output: "final.pdf",
    pages: [1, 2].map((number) => ({
      number,
      width: 600,
      height: 800,
      source_preview: `/v1/documents/doc/artifacts/source-${number}.png`,
      translated_preview: `/v1/documents/doc/artifacts/page-${number}.png`,
    })),
    regions: [
      region("small", 1, "review", [
        {
          code: "small_text",
          severity: "warning",
          message: "Text is small; inspect readability.",
        },
      ]),
      {
        ...region("retained", 1, "change", [
          {
            code: "overflow",
            severity: "error",
            message: "Translation could not fit; source retained.",
          },
        ]),
        source_rects: [[45, 250, 550, 295]],
        translated_rects: [[45, 250, 550, 295]],
        rendered: false,
      },
      {
        ...region("clear", 2, "clear"),
        source_text: '<img src=x onerror="window.injected=true">',
      },
      {
        id: "page-warning",
        page_number: 2,
        status: "review",
        source_rects: [],
        translated_rects: [],
        source_text: "",
        translated_text: null,
        rendered: null,
        issues: [
          {
            code: "graphics_changed",
            severity: "warning",
            message: "Inspect the full page.",
          },
        ],
      },
    ],
  };
}
const reviewImage = `<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800" viewBox="0 0 600 800"><rect width="600" height="800" fill="white"/><text x="45" y="55" font-size="23" font-family="serif" fill="#352f2b">Project report</text><path d="M45 75H550" stroke="#ccc"/><text x="45" y="120" font-size="13" font-family="sans-serif">The project report is due on October 31, 2026.</text><text x="45" y="140" font-size="13" font-family="sans-serif">Submit your work and demonstrate three diverse test cases.</text><text x="45" y="230" font-size="17" font-family="serif">Evaluation criteria</text><text x="45" y="270" font-size="13" font-family="sans-serif">Code quality, functionality, robustness and user experience.</text><path d="M45 330H550M45 375H550M45 420H550M45 330V420M310 330V420M550 330V420" stroke="#aaa"/><text x="60" y="358" font-size="13">Demonstration</text><text x="330" y="358" font-size="13">Online</text><text x="60" y="402" font-size="13">Submission</text><text x="330" y="402" font-size="13">October 31, 2026</text></svg>`;
async function setup(scenario = {}) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const state = {
    uploads: 0,
    translations: 0,
    jobReads: 0,
    payload: null,
    failPoll: Boolean(scenario.failPoll),
    ...scenario,
  };
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("http://studio.test/**", async (route) => {
    const url = new URL(route.request().url()),
      pathname = url.pathname;
    const json = (body, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    if (pathname === "/" || pathname.startsWith("/assets/")) {
      const file = pathname === "/" ? "index.html" : path.basename(pathname);
      return route.fulfill({
        contentType: file.endsWith(".html")
          ? "text/html"
          : file.endsWith(".css")
            ? "text/css"
            : file.endsWith(".png")
              ? "image/png"
              : "application/javascript",
        body: await fs.readFile(path.join(web, file)),
      });
    }
    if (pathname === "/v1/health")
      return state.healthDown
        ? route.abort()
        : json({ ...health, ...state.health });
    if (pathname === "/v1/documents" && route.request().method() === "POST") {
      state.uploads++;
      if (state.uploadError)
        return json(
          {
            error: { code: state.uploadError, message: "Server upload error" },
          },
          413,
        );
      return json(
        { document_id: "doc", job_id: "inspect", status: "queued" },
        202,
      );
    }
    if (pathname === "/v1/jobs/inspect")
      return json({
        status: state.inspectError ? "failed" : "completed",
        progress: 1,
        error_code: state.inspectError,
      });
    if (pathname === "/v1/documents/doc/pages")
      return json(
        state.pages || [{ metadata_json: { extraction_method: "digital" } }],
      );
    if (pathname === "/v1/documents/doc/review") {
      state.reviewReads = (state.reviewReads || 0) + 1;
      if (state.reviewFails) {
        state.reviewFails = false;
        return json(
          {
            error: {
              code: "document_busy",
              message: "Wait for the current job to finish.",
            },
          },
          409,
        );
      }
      return json(state.review || reviewFixture());
    }
    if (/\/artifacts\/(source|page)-\d+\.png$/.test(pathname)) {
      if (state.brokenPreview && pathname.includes("page-"))
        return route.fulfill({ status: 404, body: "Not ready" });
      return route.fulfill({ contentType: "image/svg+xml", body: reviewImage });
    }
    if (pathname === "/v1/documents/doc/review/repair") {
      state.repairPayload = route.request().postDataJSON();
      state.repairCalls = (state.repairCalls || 0) + 1;
      if (state.repairUnavailable)
        return json(
          {
            error: {
              code: "repair_not_configured",
              message: "Set OPENROUTER_API_KEY to enable AI repair.",
            },
          },
          503,
        );
      if (state.loseRepair && !state.repairLost) {
        state.repairLost = true;
        return route.abort();
      }
      return json({ job_id: "repair" }, 202);
    }
    if (pathname === "/v1/jobs/repair") {
      if (state.repairRunning)
        return json({ status: "running", progress: 0.4 });
      state.review = reviewFixture();
      state.review.version = "repaired-v2";
      state.review.previous_available = true;
      state.review.regions[0].translated_text = "Corrected translation";
      return json({
        status: "completed_with_issues",
        payload: {
          accepted: 1,
          repair_results: [
            {
              block_id: "small",
              status: "repaired",
              reason: "Centered the selected text",
            },
          ],
        },
      });
    }
    if (pathname === "/v1/documents/doc/translations") {
      state.translations++;
      state.payload = route.request().postDataJSON();
      if (state.lostSubmission) {
        state.lostSubmission = false;
        return route.abort();
      }
      return json({ job_id: "translate" }, 202);
    }
    if (pathname === "/v1/documents/doc/transliteration-candidates")
      return json({
        candidates: state.noWords
          ? []
          : [
              { word: "VGF", count: 2 },
              { word: "API", count: 1 },
            ],
        max_words: 20,
      });
    if (pathname === "/v1/documents/doc/transliteration-preview") {
      state.previewPayload = route.request().postDataJSON();
      state.previewCalls = (state.previewCalls || 0) + 1;
      if (state.losePreview) {
        state.losePreview = false;
        state.originalPreviewId = state.previewPayload.request_id;
        return route.abort();
      }
      return json({ job_id: "preview" }, 202);
    }
    if (pathname === "/v1/jobs/preview") {
      if (state.previewFails) {
        state.previewFails = false;
        return json({
          id: "preview",
          status: "failed",
          error_code: "transliteration_endpoint_unavailable",
          error_message: "Transliteration endpoint unavailable",
        });
      }
      return json({
        id: "preview",
        status: "completed",
        progress: 1,
        payload: {
          suggestions: state.previewPayload.words.map((word) => ({
            word,
            replacement: word === "VGF" ? "வீ ஜி எஃப்" : "ஏ பி ஐ",
          })),
        },
      });
    }
    if (pathname === "/v1/documents/doc/transliteration-apply") {
      state.applyPayload = route.request().postDataJSON();
      return json({ job_id: "render" }, 202);
    }
    if (pathname === "/v1/jobs/render")
      return json({ status: "completed", progress: 1 });
    if (pathname === "/v1/jobs/translate/cancel") {
      state.cancelCalls = (state.cancelCalls || 0) + 1;
      if (state.cancelFails) {
        state.cancelFails = false;
        return route.abort();
      }
      state.cancelled = true;
      return json({ status: "cancelling" });
    }
    if (pathname === "/v1/jobs/translate") {
      state.jobReads++;
      if (state.failPoll) return route.abort();
      return json({
        status: state.cancelled
          ? "cancelled"
          : state.jobError
            ? "failed"
            : state.running
              ? "running"
              : state.partial
                ? "completed_with_issues"
                : "completed",
        progress: state.running ?? 1,
        error_code: state.jobError,
      });
    }
    if (pathname === "/v1/documents/doc")
      return json({
        status: state.partial ? "completed_with_issues" : "completed",
        latest_job: {
          id: state.translations ? "translate" : "inspect",
          operation: state.translations ? "translate" : "inspect",
          status: "completed",
        },
        translation_summary: state.summary || {
          total_blocks: 10,
          rendered_blocks: 10,
          retained_source_blocks: 0,
        },
        issue_summary: state.issues || [],
      });
    throw new Error("Unexpected request: " + pathname);
  });
  await page.goto("http://studio.test/");
  await page.waitForFunction(
    () =>
      document.querySelector("#language").options.length > 1 ||
      !document.querySelector("#connection-note").hidden,
  );
  return { context, page, state, errors };
}
const file = {
  name: "sample.pdf",
  mimeType: "application/pdf",
  buffer: Buffer.from("%PDF-1.7 test fixture"),
};
async function start(page) {
  await page.locator("#pdf-file").setInputFiles(file);
  await page.locator("#submit").click();
}
async function completed(page) {
  await page.locator("#download").waitFor({ state: "visible" });
}
async function test(name, scenario, fn) {
  const env = await setup(scenario);
  try {
    await fn(env);
    assert.deepEqual(env.errors, []);
    console.log("PASS", name);
  } finally {
    await env.context.close();
  }
}
(async () => {
  await fs.mkdir(output, { recursive: true });
  browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROME_PATH
      ? { executablePath: process.env.CHROME_PATH }
      : {}),
  });
  await test(
    "initial view, native names, responsive layout",
    {},
    async ({ page }) => {
      assert.match(await page.locator("#language").innerText(), /தமிழ்/);
      await page.locator(".iitm-logo").evaluate((img) => img.decode());
      assert.equal(
        await page
          .locator(".iitm-logo")
          .evaluate((img) => img.naturalWidth > 0),
        true,
      );
      assert.match(
        await page.locator(".maker-credit").innerText(),
        /Krishna Karthikeyan/,
      );
      assert.equal(await page.locator('input[value="auto"]').isChecked(), true);
      assert.equal(await page.locator("#submit").isDisabled(), true);
      await page.screenshot({
        path: path.join(output, "desktop.png"),
        fullPage: true,
      });
      for (const width of [320, 390, 768, 1280]) {
        await page.setViewportSize({ width, height: 1000 });
        assert.equal(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
          true,
        );
      }
      await page.setViewportSize({ width: 390, height: 1000 });
      await page.screenshot({
        path: path.join(output, "mobile.png"),
        fullPage: true,
      });
    },
  );
  await test(
    "visual issue review, paired highlights, decisions and output version",
    {},
    async ({ page, state }) => {
      await page.setViewportSize({ width: 1500, height: 1100 });
      await start(page);
      await completed(page);
      await page.locator("#see-issues").click();
      await page.locator("#review-content").waitFor({ state: "visible" });
      await page.locator("#review-source").evaluate((img) => img.decode());
      assert.equal(await page.locator(".review-region").count(), 2);
      assert.equal(
        await page.locator("#source-highlights .selected").count(),
        1,
      );
      assert.equal(
        await page.locator("#translated-highlights .selected").count(),
        1,
      );
      assert.match(
        await page.locator("#review-bar").getAttribute("aria-label"),
        /1 need attention, 1 to check visually, 1 with no automatic issue/,
      );
      await page.locator("#review-okay").click();
      assert.equal(
        await page.locator('.review-region[data-region-id="small"]').count(),
        0,
      );
      assert.equal(
        await page
          .locator('#source-highlights [data-region-id="small"]')
          .count(),
        0,
      );
      assert.equal(
        await page
          .locator('.review-region[aria-pressed="true"]')
          .getAttribute("data-region-id"),
        "retained",
      );
      assert.match(
        await page.locator("#review-counts").innerText(),
        /2 pending findings/,
      );
      await page.locator("#review-filter").selectOption("okay");
      assert.equal(
        await page.locator("#source-highlights .region-okay").count(),
        1,
      );
      assert.match(
        await page.locator("#review-region-status").innerText(),
        /Automatic: Check visually/,
      );
      await page.locator("#close-review").click();
      await page.locator("#see-issues").click();
      await page.locator("#review-content").waitFor({ state: "visible" });
      assert.equal(
        await page.locator('.review-region[data-region-id="small"]').count(),
        0,
      );
      await page.locator("#review-filter").selectOption("okay");
      assert.equal(
        await page.locator("#review-okay").getAttribute("aria-pressed"),
        "true",
      );
      await page.locator("#review-reset").click();
      assert.equal(await page.locator(".review-region").count(), 0);
      await page.locator("#review-filter").selectOption("flagged");
      assert.equal(
        await page.locator('.review-region[data-region-id="small"]').count(),
        1,
      );
      await page.locator("#review-okay").click();
      await page.locator('.review-region[data-region-id="retained"]').click();
      assert.match(
        await page.locator("#review-region-status").innerText(),
        /Source text is retained/,
      );
      await page.locator("#review-change").click();
      assert.equal(await page.locator(".review-region").count(), 0);
      assert.equal(
        await page.locator("#source-highlights .region-marker").count(),
        0,
      );
      assert.equal(await page.locator("#review-detail").isVisible(), false);
      assert.match(
        await page.locator("#review-counts").innerText(),
        /1 pending findings/,
      );
      assert.match(
        await page.locator("#review-counts").innerText(),
        /1 queued for changes/,
      );
      await page.locator("#review-filter").selectOption("human-change");
      assert.equal(await page.locator(".review-region").count(), 1);
      assert.match(
        await page.locator(".review-region").innerText(),
        /Queued for changes/,
      );
      assert.equal(
        await page.locator("#review-change").getAttribute("aria-pressed"),
        "true",
      );
      await page.locator("#review-filter").selectOption("flagged");
      await page.locator("#review-zoom").selectOption("2");
      await page.locator("#source-scroll").evaluate((node) => {
        node.scrollTop = 100;
        node.scrollLeft = 50;
      });
      await page.waitForFunction(
        () => document.querySelector("#translated-scroll").scrollTop > 50,
      );
      await page.locator("#review-zoom").selectOption("1");
      await page.locator("#document-review").scrollIntoViewIfNeeded();
      await page
        .locator("#document-review")
        .screenshot({ path: path.join(output, "side-by-side-review.png") });
      await page.locator("#review-next").click();
      assert.equal(await page.locator(".review-region").count(), 1);
      await page.locator(".review-region").click();
      assert.match(
        await page.locator("#review-region-status").innerText(),
        /No precise region/,
      );
      assert.equal(
        await page.locator("#source-highlights .selected").count(),
        0,
      );
      await page.locator("#review-filter").selectOption("all");
      await page.locator('#source-highlights [data-region-id="clear"]').click();
      assert.match(
        await page.locator("#review-source-text").innerText(),
        /<img/,
      );
      assert.equal(await page.evaluate(() => Boolean(window.injected)), false);
      await page.locator("#review-highlights").uncheck();
      assert.equal(await page.locator("#source-highlights").isVisible(), false);
      await page.locator("#review-highlights").check();
      for (const width of [320, 390, 768, 1280]) {
        await page.setViewportSize({ width, height: 1000 });
        assert.equal(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
          true,
        );
      }
      await page.setViewportSize({ width: 390, height: 1000 });
      await page.locator("#document-review").screenshot({
        path: path.join(output, "side-by-side-review-mobile.png"),
      });
      state.review = reviewFixture();
      state.review.version = "v2";
      await page.locator("#close-review").click();
      await page.locator("#see-issues").click();
      await page.locator("#review-content").waitFor({ state: "visible" });
      assert.equal(
        await page.locator("#review-okay").getAttribute("aria-pressed"),
        "false",
      );
      assert.equal(state.translations, 1);
      await page.locator("#new-document").click();
      assert.equal(await page.locator("#document-review").isVisible(), false);
    },
  );
  await test(
    "AI repairs only queued regions and final download follows results",
    {},
    async ({ page, state }) => {
      await start(page);
      await completed(page);
      await page.locator("#see-issues").click();
      await page.locator("#review-content").waitFor({ state: "visible" });
      assert.equal(await page.locator("#repair-marked").isDisabled(), true);
      await page.locator("#review-change").click();
      await page.locator("#review-okay").click();
      await page
        .locator("#repair-instruction")
        .fill("Center the selected text");
      await page.locator("#repair-marked").click();
      await page.waitForFunction(() =>
        document
          .querySelector("#repair-status")
          .textContent.includes("1 region repaired"),
      );
      assert.deepEqual(state.repairPayload.region_ids, ["small"]);
      assert.equal(state.repairPayload.instruction, "Center the selected text");
      assert.equal(state.repairCalls, 1);
      assert.match(
        await page.locator("#repair-results").innerText(),
        /Repaired — review again/,
      );
      assert.match(
        await page.locator("#review-final-download").getAttribute("href"),
        /final.pdf.*repaired-v2/,
      );
      assert.equal(
        await page.locator("#review-previous-download").isVisible(),
        true,
      );
      assert.equal(await page.locator("#repair-marked").isDisabled(), true);
      assert.equal(
        await page.evaluate(
          () =>
            document
              .querySelector("#repair-results")
              .compareDocumentPosition(
                document.querySelector("#review-final-download"),
              ) & Node.DOCUMENT_POSITION_FOLLOWING,
        ),
        4,
      );
    },
  );
  await test(
    "AI repair unavailable retains queue and download",
    { repairUnavailable: true },
    async ({ page }) => {
      await start(page);
      await completed(page);
      await page.locator("#see-issues").click();
      await page.locator("#review-content").waitFor({ state: "visible" });
      await page.locator("#review-change").click();
      await page.locator("#repair-marked").click();
      await page.waitForFunction(() =>
        document
          .querySelector("#repair-status")
          .textContent.includes("OPENROUTER_API_KEY"),
      );
      assert.match(
        await page.locator("#repair-queue-count").innerText(),
        /1 region queued/,
      );
      assert.match(
        await page.locator("#review-final-download").getAttribute("href"),
        /final.pdf.*v1/,
      );
    },
  );
  await test(
    "lost AI submission reconnects using the same request",
    { loseRepair: true },
    async ({ page, state }) => {
      await start(page);
      await completed(page);
      await page.locator("#see-issues").click();
      await page.locator("#review-content").waitFor({ state: "visible" });
      await page.locator("#review-change").click();
      await page.locator("#repair-marked").click();
      await page.waitForFunction(() =>
        document.querySelector("#repair-status").textContent.includes("Reopen"),
      );
      const requestId = state.repairPayload.request_id;
      await page.locator("#close-review").click();
      await page.locator("#see-issues").click();
      await page.waitForFunction(() =>
        document
          .querySelector("#repair-status")
          .textContent.includes("1 region repaired"),
      );
      assert.equal(state.repairPayload.request_id, requestId);
    },
  );
  await test(
    "AI repair resumes after browser reload without duplicate submission",
    { repairRunning: true },
    async ({ page, state }) => {
      await start(page);
      await completed(page);
      await page.locator("#see-issues").click();
      await page.locator("#review-content").waitFor({ state: "visible" });
      await page.locator("#review-change").click();
      await page.locator("#repair-marked").click();
      await page.waitForFunction(() =>
        document.querySelector("#repair-status").textContent.includes("40%"),
      );
      await page.reload();
      await page.waitForFunction(() =>
        document.querySelector("#repair-status").textContent.includes("40%"),
      );
      state.repairRunning = false;
      await page.waitForFunction(() =>
        document
          .querySelector("#repair-status")
          .textContent.includes("1 region repaired"),
      );
      assert.equal(state.repairCalls, 1);
      await page
        .locator(".review-repair")
        .screenshot({ path: path.join(output, "ai-repair-and-download.png") });
    },
  );
  await test(
    "failed pronunciation preview keeps PDF available and retry uses fresh request",
    { previewFails: true },
    async ({ page, state }) => {
      await page.locator("#language").selectOption("ta");
      await start(page);
      await completed(page);
      await page.locator("#transliterate").click();
      await page.locator("#transliteration-words input").first().check();
      await page.locator("#generate-transliteration").click();
      await page.waitForFunction(() =>
        document
          .querySelector("#status-title")
          .textContent.includes("preview could not finish"),
      );
      const failedId = state.previewPayload.request_id;
      assert.equal(await page.locator("#download").isVisible(), true);
      await page.locator("#generate-transliteration").click();
      await page
        .locator("#apply-transliteration")
        .waitFor({ state: "visible" });
      assert.notEqual(state.previewPayload.request_id, failedId);
    },
  );
  await test(
    "old output explains that a fresh paragraph translation is needed",
    {},
    async ({ page, state }) => {
      state.review = reviewFixture();
      state.review.regroup_recommended = true;
      await start(page);
      await completed(page);
      await page.locator("#see-issues").click();
      await page.locator("#review-layout-note").waitFor({ state: "visible" });
      assert.match(
        await page.locator("#review-layout-note").innerText(),
        /fresh translation/,
      );
      state.review.regroup_recommended = false;
      await page.locator("#close-review").click();
      await page.locator("#see-issues").click();
      await page.locator("#review-content").waitFor({ state: "visible" });
      assert.equal(
        await page.locator("#review-layout-note").isVisible(),
        false,
      );
    },
  );
  await test(
    "review retries and missing page previews",
    { reviewFails: true, brokenPreview: true },
    async ({ page }) => {
      await start(page);
      await completed(page);
      await page.locator("#see-issues").click();
      await page.locator("#retry-review").waitFor({ state: "visible" });
      assert.equal(await page.locator("#download").isVisible(), true);
      await page.locator("#retry-review").click();
      await page
        .locator("#translated-unavailable")
        .waitFor({ state: "visible" });
      assert.equal(await page.locator("#translated-canvas").isVisible(), false);
      assert.equal(await page.locator("#source-canvas").isVisible(), true);
    },
  );
  await test(
    "initial transliteration option follows language and reaches request",
    {},
    async ({ page, state }) => {
      await page.locator("#language").selectOption("en");
      assert.equal(
        await page.locator("#auto-transliterate").isDisabled(),
        true,
      );
      await page.locator("#language").selectOption("ta");
      await page.locator("#auto-transliterate").check();
      await start(page);
      await completed(page);
      assert.equal(state.payload.transliterate, true);
    },
  );
  await test(
    "reject wrong type and empty PDF before upload",
    {},
    async ({ page, state }) => {
      await page
        .locator("#pdf-file")
        .setInputFiles({ ...file, name: "notes.txt" });
      assert.match(
        await page.locator("#file-error").innerText(),
        /Choose a PDF/,
      );
      await page
        .locator("#pdf-file")
        .setInputFiles({ ...file, buffer: Buffer.alloc(0) });
      assert.match(await page.locator("#file-error").innerText(), /empty/);
      assert.equal(state.uploads, 0);
    },
  );
  await test(
    "reject oversized PDF before upload",
    { health: { max_upload_mb: 1 } },
    async ({ page, state }) => {
      await page
        .locator("#pdf-file")
        .setInputFiles({ ...file, buffer: Buffer.alloc(1048577) });
      assert.match(
        await page.locator("#file-error").innerText(),
        /exceeds the 1 MB limit/,
      );
      assert.equal(state.uploads, 0);
    },
  );
  for (const layout of ["auto", "paragraphs", "spans"])
    await test(
      "successful " + layout + " request",
      {},
      async ({ page, state }) => {
        await page.locator(`input[value="${layout}"]`).check();
        await start(page);
        await completed(page);
        assert.equal(state.payload.layout_mode, layout);
        assert.equal(state.translations, 1);
        assert.equal(
          await page.locator("#download").getAttribute("href"),
          "/v1/documents/doc/artifacts/final.pdf?v=translate",
        );
        assert.equal(await page.locator("#review-note").isVisible(), false);
        await page.locator("#new-document").click();
        assert.equal(await page.locator("#result").isVisible(), false);
        assert.equal(await page.locator("#submit").isDisabled(), true);
      },
    );
  await test(
    "scan, blank page and retained-source warnings",
    {
      partial: true,
      pages: [
        { metadata_json: { extraction_method: "ocr" } },
        { metadata_json: { extraction_method: "blank" } },
        { metadata_json: { extraction_method: "digital" } },
      ],
      summary: {
        total_blocks: 10,
        rendered_blocks: 8,
        retained_source_blocks: 2,
      },
      issues: [
        { code: "unsupported_glyphs" },
        { code: "collision" },
        { code: "ocr_layout_estimated" },
      ],
    },
    async ({ page }) => {
      await start(page);
      await completed(page);
      assert.match(
        await page.locator("#document-note").innerText(),
        /1 scanned.*1 blank/,
      );
      assert.match(
        await page.locator("#review-summary").innerText(),
        /2 text regions remain/,
      );
      assert.equal(await page.locator("#review-reasons li").count(), 3);
      await page.screenshot({
        path: path.join(output, "review.png"),
        fullPage: true,
      });
    },
  );
  await test(
    "blank PDF does not claim translation",
    {
      summary: {
        total_blocks: 0,
        rendered_blocks: 0,
        retained_source_blocks: 0,
      },
    },
    async ({ page }) => {
      await start(page);
      await completed(page);
      assert.match(
        await page.locator("#status-title").innerText(),
        /no text found/,
      );
    },
  );
  for (const [code, pattern] of [
    ["encrypted_pdf", /password-protected/],
    ["too_many_pages", /100-page limit/],
    ["invalid_pdf", /fresh PDF/],
    ["ocr_authentication_failed", /OCR credentials/],
  ])
    await test(code, { inspectError: code }, async ({ page, state }) => {
      await start(page);
      await page.locator("#new-document").waitFor({ state: "visible" });
      assert.match(await page.locator("#status-detail").innerText(), pattern);
      assert.equal(state.translations, 0);
      assert.equal(await page.locator("#download").isVisible(), false);
    });
  for (const [code, pattern] of [
    ["provider_unavailable", /after retries/],
    ["invalid_api_key", /credentials/],
  ])
    await test(code, { jobError: code }, async ({ page }) => {
      await start(page);
      await page.locator("#new-document").waitFor({ state: "visible" });
      assert.match(await page.locator("#status-detail").innerText(), pattern);
    });
  await test(
    "reconstruction failure exposes draft only",
    { jobError: "reconstruction_failed" },
    async ({ page }) => {
      await start(page);
      await page.locator("#draft").waitFor({ state: "visible" });
      assert.equal(await page.locator("#download").isVisible(), false);
      assert.equal(
        await page.locator("#draft").getAttribute("href"),
        "/v1/documents/doc/artifacts/draft.pdf",
      );
    },
  );
  await test(
    "lost submission response reconnects without duplicate POST",
    { lostSubmission: true },
    async ({ page, state }) => {
      await start(page);
      await page.locator("#resume").waitFor({ state: "visible" });
      assert.equal(await page.locator("#submit").isDisabled(), true);
      await page.locator("#resume").click();
      await completed(page);
      assert.equal(state.translations, 1);
    },
  );
  await test(
    "poll outage survives page reload without new translation",
    { failPoll: true },
    async ({ page, state }) => {
      await page.locator('input[value="spans"]').check();
      await start(page);
      await page.locator("#resume").waitFor({ state: "visible" });
      state.failPoll = false;
      await page.reload();
      await completed(page);
      assert.equal(
        await page.locator('input[value="spans"]').isChecked(),
        true,
      );
      assert.equal(state.uploads, 1);
      assert.equal(state.translations, 1);
    },
  );
  await test(
    "rendering stage locks language and layout controls",
    { running: 0.8 },
    async ({ page, state }) => {
      await start(page);
      await page.waitForFunction(
        () =>
          document.querySelector("#status-title").textContent ===
          "Preparing your download",
      );
      assert.equal(
        await page.locator('input[value="spans"]').isDisabled(),
        true,
      );
      assert.equal(await page.locator("#language").isDisabled(), true);
      assert.equal(
        await page.locator('[data-step="render"]').getAttribute("aria-current"),
        "step",
      );
      state.running = false;
      await completed(page);
    },
  );
  await test(
    "cancel stops a running translation and unlocks controls",
    { running: 0.3 },
    async ({ page, state }) => {
      await start(page);
      await page.waitForFunction(
        () =>
          document.querySelector("#status-title").textContent ===
          "Translating your document",
      );
      await page.locator("#cancel").click();
      await page.waitForFunction(
        () =>
          document.querySelector("#status-title").textContent ===
          "Translation cancelled",
      );
      assert.equal(state.cancelCalls, 1);
      assert.equal(await page.locator("#download").isVisible(), false);
      assert.equal(await page.locator("#cancel").isVisible(), false);
      assert.equal(await page.locator("#language").isDisabled(), false);
      assert.equal(
        await page.evaluate(() =>
          sessionStorage.getItem("translation-studio-active-job"),
        ),
        null,
      );
    },
  );
  await test(
    "cancel can be retried after a lost response",
    { running: 0.3, cancelFails: true },
    async ({ page, state }) => {
      await start(page);
      await page.waitForFunction(
        () =>
          document.querySelector("#status-title").textContent ===
          "Translating your document",
      );
      await page.locator("#cancel").click();
      await page.waitForFunction(() =>
        document
          .querySelector("#status-detail")
          .textContent.includes("Could not confirm cancellation"),
      );
      assert.equal(await page.locator("#cancel").isEnabled(), true);
      await page.locator("#cancel").click();
      await page.waitForFunction(
        () =>
          document.querySelector("#status-title").textContent ===
          "Translation cancelled",
      );
      assert.equal(state.cancelCalls, 2);
    },
  );
  await test(
    "remaining English can be selected, previewed and applied",
    {},
    async ({ page, state }) => {
      await page.locator("#language").selectOption("ta");
      await start(page);
      await completed(page);
      await page.locator("#transliterate").click();
      assert.equal(
        await page.locator("#generate-transliteration").isDisabled(),
        true,
      );
      await page.locator('#transliteration-words input[value="VGF"]').check();
      await page.locator('#transliteration-words input[value="API"]').check();
      await page.locator("#generate-transliteration").click();
      await page
        .locator("#apply-transliteration")
        .waitFor({ state: "visible" });
      assert.deepEqual(state.previewPayload.words, ["VGF", "API"]);
      assert.match(
        await page.locator("#transliteration-words").innerText(),
        /வீ ஜி எஃப்/,
      );
      assert.equal(state.applyPayload, undefined);
      await page.screenshot({
        path: path.join(output, "transliteration.png"),
        fullPage: true,
      });
      await page.locator('#transliteration-words input[value="API"]').uncheck();
      await page.locator("#apply-transliteration").click();
      await completed(page);
      assert.deepEqual(state.applyPayload, {
        preview_job_id: "preview",
        words: ["VGF"],
      });
    },
  );
  await test(
    "no remaining English makes no transliteration call",
    { noWords: true },
    async ({ page, state }) => {
      await start(page);
      await completed(page);
      await page.locator("#transliterate").click();
      await page.waitForFunction(() =>
        document
          .querySelector("#transliteration-message")
          .textContent.includes("No eligible English"),
      );
      assert.equal(
        await page.locator("#generate-transliteration").isVisible(),
        false,
      );
      assert.equal(state.previewCalls, undefined);
    },
  );
  await test(
    "lost pronunciation submission reuses its request ID",
    { losePreview: true },
    async ({ page, state }) => {
      await start(page);
      await completed(page);
      await page.locator("#transliterate").click();
      await page.locator('#transliteration-words input[value="VGF"]').check();
      await page.locator("#generate-transliteration").click();
      await page.waitForFunction(() =>
        document
          .querySelector("#transliteration-message")
          .textContent.includes("connection"),
      );
      await page.locator("#generate-transliteration").click();
      await page
        .locator("#apply-transliteration")
        .waitFor({ state: "visible" });
      assert.equal(state.previewPayload.request_id, state.originalPreviewId);
    },
  );
  await test(
    "legacy repair status has no UI controls",
    { health: { vision_review_enabled: true } },
    async ({ page }) => {
      assert.equal(await page.locator("#vision-note").count(), 0);
    },
  );
  await test(
    "missing fonts are explained",
    { health: { available_font_languages: ["en"] } },
    async ({ page }) => {
      assert.match(
        await page.locator("#font-note").innerText(),
        /font is not installed/,
      );
    },
  );
  await test(
    "missing service configuration is explained",
    { health: { translation_configured: false } },
    async ({ page }) => {
      assert.match(
        await page.locator("#connection-message").innerText(),
        /not configured/,
      );
      await page.locator("#pdf-file").setInputFiles(file);
      assert.equal(await page.locator("#submit").isDisabled(), true);
    },
  );
  await test(
    "server startup connection can be retried",
    { healthDown: true },
    async ({ page, state }) => {
      assert.match(
        await page.locator("#connection-message").innerText(),
        /Cannot connect/,
      );
      state.healthDown = false;
      await page.locator("#reconnect").click();
      await page.waitForFunction(
        () => document.querySelector("#connection-note").hidden,
      );
    },
  );
  console.log("All browser checks passed. Screenshots:", output);
})()
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(async () => {
    if (browser) await browser.close();
  });
