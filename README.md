# Cockpit Yannis

Personal health cockpit — static mockup phase.

Live: https://gogetenk.github.io/yannis-cockpit/

- `index.html` — high-fi mockup (mobile-first, sage design system)
- `PRODUCT.md` — strategic context (users, voice, anti-refs, principles)
- `DESIGN.md` — visual system (The Sage Cabinet, OKLCH tokens, type)
- `screenshot.mjs` — Playwright visual harness (4 viewports → `shots/`)

## Resume on another machine

```bash
git clone https://github.com/gogetenk/yannis-cockpit
cd yannis-cockpit
npm i playwright && npx playwright install chromium
node screenshot.mjs
```

Open `index.html` directly or serve via any static host.
