import { copyFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const directory = dirname(fileURLToPath(import.meta.url));
const files = [
  "index.html", "styles.css", "robots.txt", "sitemap.xml",
  "assets/logo.svg", "assets/favicon.svg", "assets/favicon-16.png",
  "assets/favicon-32.png", "assets/apple-touch-icon.png",
  "assets/og-image.png", "assets/project-thumbnail.png",
  "assets/frontend-desktop.png", "assets/frontend-mobile.png",
];

for (const file of files) {
  const output = join(directory, "dist", file);
  mkdirSync(dirname(output), { recursive: true });
  copyFileSync(join(directory, "site", file), output);
}
console.log("Built " + files.length + " static files in showcase/dist.");
