import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("./dist/", import.meta.url));
const text = (name) => readFileSync(resolve(root, name), "utf8");
const page = text("index.html");
const canonical = "https://rag.mubashirhussain.dev/";

test("build ships only static showcase files", () => {
  assert.deepEqual(readdirSync(root).sort(), ["assets", "index.html", "robots.txt", "sitemap.xml", "styles.css"]);
  assert.doesNotMatch(page, /<iframe\b|<form\b|<script\s+src=/i);
  assert.equal((page.match(/<script\b/g) || []).length, 1);
  assert.match(page, /No hosted inference/);
});

test("metadata, structured data and crawler files agree", () => {
  assert.match(page, /<html lang="en"/);
  assert.match(page, /<title>Local RAG Customer Support Agent \| Mubashir Hussain<\/title>/);
  assert.match(page, /name="viewport" content="width=device-width, initial-scale=1"/);
  assert.match(page, /name="description" content="[^"]{70,160}"/);
  assert.ok(page.includes('rel="canonical" href="' + canonical + '"'));
  assert.ok(page.includes('property="og:url" content="' + canonical + '"'));
  for (const field of ["title", "description", "image", "image:alt"]) {
    assert.ok(page.includes('property="og:' + field + '"'));
    assert.ok(page.includes('name="twitter:' + field + '"'));
  }
  assert.match(page, /name="twitter:card" content="summary_large_image"/);
  const schema = JSON.parse(page.match(/<script type="application\/ld\+json">([\s\S]+?)<\/script>/)[1]);
  assert.equal(schema.url, canonical);
  assert.equal(schema.mainEntity["@type"], "SoftwareSourceCode");
  assert.ok(text("robots.txt").includes("Sitemap: " + canonical + "sitemap.xml"));
  assert.ok(text("sitemap.xml").includes("<loc>" + canonical + "</loc>"));
});

test("images, anchors, local assets and raster dimensions", () => {
  const ids = new Set(Array.from(page.matchAll(/\sid="([^"]+)"/g), (m) => m[1]));
  for (const [, target] of page.matchAll(/(?:href|src)="([^"]+)"/g)) {
    if (target.startsWith("#")) assert.ok(ids.has(target.slice(1)), target);
    else if (target.startsWith("./")) assert.ok(readFileSync(resolve(root, target)).length > 0, target);
    else assert.equal(new URL(target).protocol, "https:");
  }
  for (const img of page.matchAll(/<img\b[^>]+>/g)) assert.match(img[0], /\balt="/);
  for (const [name, width, height] of [
    ["favicon-16", 16, 16], ["favicon-32", 32, 32], ["apple-touch-icon", 180, 180],
    ["og-image", 1200, 630], ["project-thumbnail", 960, 540], ["frontend-desktop", 1440, 1237],
  ]) {
    const bytes = readFileSync(resolve(root, "assets", name + ".png"));
    assert.equal(bytes.toString("hex", 0, 8), "89504e470d0a1a0a");
    assert.deepEqual([bytes.readUInt32BE(16), bytes.readUInt32BE(20)], [width, height], name);
  }
  assert.equal(text("assets/logo.svg"), text("assets/favicon.svg"));
});
