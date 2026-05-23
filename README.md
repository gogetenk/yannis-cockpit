# Cockpit Yannis

Personal health cockpit — mobile-first PWA.

- `app/`, `components/`, `lib/` — Next.js 15 (App Router) + React 19 + Tailwind v4 + shadcn-style primitives
- `app/api/cockpit` — single read endpoint, returns the latest snapshot
- `supabase/` — SQL schema + seed (`cockpit_snapshot` table holds one JSONB blob per day)
- `mockup/` — original static HTML (`index.html`) + Playwright screenshot harness (kept for reference)
- `PRODUCT.md` / `DESIGN.md` — strategic + visual system
- `ALGO_BIO_AGE.md` / `ALGO_SIGNAUX.md` — methodology with peer-reviewed citations

## Run locally

```bash
npm install
npm run dev   # → http://localhost:3000
```

The app runs out of the box with the frozen mock snapshot (mirroring the current mockup). To wire it to Supabase, copy `.env.local.example` to `.env.local` and fill in:

```
NEXT_PUBLIC_SUPABASE_URL=https://...supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
```

Then apply the schema in the Supabase SQL editor:

```sql
\i supabase/migrations/0001_init.sql
\i supabase/seed.sql
```

When `SUPABASE_SERVICE_ROLE_KEY` is missing, the API route falls back to the mock — no setup needed to develop.

## API

```
GET /api/cockpit
→ { "source": "supabase" | "mock", "data": CockpitSnapshot }
```

See `lib/types.ts` for the snapshot shape. Ingestion (Yazio / Withings / Huawei / labs → snapshot) is the next phase.

## Mockup harness

```bash
npm run shot   # writes mockup/shots/*.png
```
