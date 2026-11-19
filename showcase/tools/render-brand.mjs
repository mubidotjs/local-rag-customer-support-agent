import { readFile, copyFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { join, resolve } from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.SHOWCASE_PLAYWRIGHT || "playwright");
const assets = fileURLToPath(new URL("../site/assets/", import.meta.url));
const captures = process.argv[2];
if (captures) {
  for (const layout of ["desktop", "mobile"]) {
    await copyFile(resolve(captures, "actual-local-" + layout + ".png"), join(assets, "frontend-" + layout + ".png"));
  }
}
await copyFile(join(assets, "logo.svg"), join(assets, "favicon.svg"));
const mark = "data:image/svg+xml;base64," + (await readFile(join(assets, "logo.svg"))).toString("base64");
const capture = "data:image/png;base64," + (await readFile(join(assets, "frontend-desktop.png"))).toString("base64");
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  for (const [name, size] of [["favicon-16", 16], ["favicon-32", 32], ["apple-touch-icon", 180]]) {
    await page.setViewportSize({ width: size, height: size });
    await page.setContent('<style>body{margin:0}img{display:block;width:100vw;height:100vh}</style><img src="' + mark + '">');
    await page.screenshot({ path: join(assets, name + ".png"), omitBackground: true });
  }
  for (const [name, width, height] of [["og-image", 1200, 630], ["project-thumbnail", 960, 540]]) {
    await page.setViewportSize({ width, height });
    await page.setContent(`<style>
      *{box-sizing:border-box}
      body{margin:0;background:#ecf0e5;color:#142e24;display:grid;grid-template-columns:45% 55%;align-items:center;min-height:100vh;padding:4.6vw;font:600 1.5vw/1.35 system-ui}
      main{padding-right:1vw}
      .brand{display:flex;align-items:center;gap:1vw;font-size:1.6vw}.brand img{width:4.2vw;height:4.2vw}
      h1{font:500 6.75vw/.91 Georgia;letter-spacing:-.065em;margin:2.2vw 0 1.6vw}em{color:#235941}
      p{margin:.5vw 0;color:#354e3f}
      .owner{margin-top:2vw;font:600 1.25vw Consolas,monospace;color:#35664f}
      figure{margin:0;background:#d6e4d4;padding:1.25vw;border:1px solid #bacdbb;border-radius:1vw;box-shadow:0 1.6vw 3.2vw #1c3b261c}
      figure p{font:600 1vw Consolas;margin:.3vw 0 .8vw}
      figure img{display:block;width:100%;height:32vw;object-fit:cover;object-position:center 71%;border:1px solid #bccabc;border-radius:.5vw}
      </style><main><div class="brand"><img src="${mark}">LOCAL RAG</div>
      <h1>Answers you<br>can <em>trace.</em></h1><p>Local RAG customer support agent</p>
      <p class="owner">MUBASHIR HUSSAIN</p></main>
      <figure><p>REAL LOCAL FRONTEND · FACTORY RESET</p><img src="${capture}"></figure>`);
    await page.screenshot({ path: join(assets, name + ".png") });
  }
} finally {
  await browser.close();
}
