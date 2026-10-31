// Render complete model-authored HTML documents without executing their scripts.
// Usage: NODE_PATH=/path/to/node_modules node scripts/render_html_pair.cjs DIRECTORY
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

async function main() {
  if (!process.argv[2])
    throw new Error(
      "Provide the directory containing source.html and reference.html",
    );
  const checkOnly = process.argv[2] === "--check";
  const directory = path.resolve(process.argv[2]);
  const launch = { headless: true };
  if (process.env.CHROME_PATH) launch.executablePath = process.env.CHROME_PATH;
  else if (process.platform === "darwin")
    launch.executablePath =
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const browser = await chromium.launch(launch);
  try {
    if (checkOnly) return;
    const context = await browser.newContext({
      javaScriptEnabled: false,
      bypassCSP: true,
      serviceWorkers: "block",
    });
    await context.route("**/*", (route) => route.abort());
    const page = await context.newPage();
    page.setDefaultTimeout(30000);
    for (const stem of ["source", "reference"]) {
      let html = await fs.readFile(
        path.join(directory, stem + ".html"),
        "utf8",
      );
      const fonts = await fs
        .readFile(path.join(directory, stem + "-fonts.css"), "utf8")
        .catch((e) => {
          if (e.code === "ENOENT") return "";
          throw e;
        });
      if (fonts) {
        const style = "<style>" + fonts + "</style>";
        html = /<\/head\s*>/i.test(html)
          ? html.replace(/<\/head\s*>/i, style + "</head>")
          : style + html;
      }
      await page.setContent(html, { waitUntil: "load" });
      await page.emulateMedia({ media: "print" });
      await page.evaluate(() => document.fonts.ready);
      await page.pdf({
        path: path.join(directory, stem + ".pdf"),
        format: "A4",
        printBackground: true,
        preferCSSPageSize: true,
      });
      console.log(stem + ".pdf created");
    }
  } finally {
    await browser.close();
  }
}
main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
