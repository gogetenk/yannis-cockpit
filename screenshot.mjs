// Quick playwright screenshot harness for the cockpit mockup.
// Usage:  node screenshot.mjs [path-to-html]  (default: ./aujourd-hui.html)
//
// Generates PNGs at mobile / tablet / desktop widths under ./shots/.
// Run from C:/repos/yazio-exporter/mockups so paths resolve cleanly.

import { chromium } from "playwright";
import path from "path";
import { fileURLToPath, pathToFileURL } from "url";
import fs from "fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const target = process.argv[2] || path.join(__dirname, "aujourd-hui.html");
const outDir = path.join(__dirname, "shots");
fs.mkdirSync(outDir, { recursive: true });

const url = pathToFileURL(target).href;

const viewports = [
  { name: "mobile-390",  width: 390,  height: 844  },
  { name: "tablet-768",  width: 768,  height: 1024 },
  { name: "desktop-1280", width: 1280, height: 900  },
  { name: "desktop-1920", width: 1920, height: 1080 },
];

(async () => {
  const browser = await chromium.launch();
  for (const vp of viewports) {
    const ctx = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      deviceScaleFactor: 2,
    });
    const page = await ctx.newPage();
    await page.goto(url, { waitUntil: "networkidle" });
    // Give web fonts a moment.
    await page.waitForTimeout(400);
    const file = path.join(outDir, `${vp.name}.png`);
    await page.screenshot({ path: file, fullPage: true });
    console.log(`shot ${vp.name} → ${file}`);
    await ctx.close();
  }
  await browser.close();
})();
