import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve, extname } from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.SHOWCASE_PLAYWRIGHT || "playwright");
const root = fileURLToPath(new URL("../dist/", import.meta.url));
const origin = "https://showcase.test";
const types = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml" };
const browser = await chromium.launch();
try {
  for (const width of [320, 390, 768, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) {
        errors.push("Unexpected external request: " + url.href);
        await route.abort();
        return;
      }
      const file = resolve(root, "." + (url.pathname === "/" ? "/index.html" : url.pathname));
      const body = await readFile(file);
      await route.fulfill({ body, contentType: types[extname(file)] });
    });
    await page.goto(origin + "/");
    assert.equal(await page.getByRole("heading", { level: 1 }).count(), 1);
    assert.equal(await page.getByRole("main").count(), 1);
    assert.equal(await page.getByRole("navigation", { name: "Sections" }).count(), 1);
    assert.ok(await page.getByRole("img", { name: /factory reset answer/i }).evaluate((img) => img.complete && img.naturalWidth > 0));
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.keyboard.press("Tab");
    assert.equal(await page.locator(":focus").innerText(), "Skip to content");
    await page.keyboard.press("Enter");
    assert.equal(await page.locator(":focus").getAttribute("id"), "main");
    await page.getByRole("link", { name: "Evidence", exact: true }).click();
    assert.equal(new URL(page.url()).hash, "#evidence");
    assert.deepEqual(errors, []);
    if (process.env.SHOWCASE_CAPTURE_DIR) {
      await page.goto(origin + "/");
      await page.screenshot({ path: resolve(process.env.SHOWCASE_CAPTURE_DIR, "showcase-" + width + ".png"), fullPage: true });
    }
    await page.close();
    console.log(width + "px: layout, landmarks, images, keyboard and anchor navigation passed");
  }
} finally {
  await browser.close();
}
