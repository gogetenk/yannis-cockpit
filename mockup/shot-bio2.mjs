import { chromium } from "playwright";
const browser = await chromium.launch();
for (const vp of [{n:"bio-top-390",w:390,h:1600},{n:"bio-top-1280",w:1280,h:1600}]) {
  const ctx = await browser.newContext({ viewport:{width:vp.w,height:vp.h}, deviceScaleFactor:2 });
  const page = await ctx.newPage();
  await page.goto("https://yannis-cockpit.vercel.app/detail/biology",{waitUntil:"networkidle"});
  await page.waitForTimeout(500);
  await page.screenshot({path:`mockup/shots/${vp.n}.png`, fullPage:false});
  console.log(vp.n);
  await ctx.close();
}
await browser.close();
