# Cockpit Yannis

Personal health cockpit — mobile-first PWA backed by real data from Withings, Yazio and Huawei (via the Android companion app).

**Live:** https://yannis-cockpit.vercel.app

## Architecture

```
Withings (8 ans backfill, cron 3h) ───┐
Yazio (875 jours, cron quotidien)  ───┤
Health Connect (sleep + HRV + HR continu) ─┐
   ↑                                       │
   Huawei Watch GT2 → Health Sync ──────── │
                                           │
   Android APK (HealthCockpitSync) ────────┤
                                           ▼
                                  Supabase Postgres (raw tables)
                                           │
                            build_snapshot.py (hourly cron)
                                           ▼
                                  cockpit_snapshot (JSONB)
                                           │
                                           ▼
                          Next.js app on Vercel (Sage Cabinet)
```

## Layout

- `app/`, `components/`, `lib/` — Next.js 15 (App Router) + React 19 + Tailwind v4
- `app/api/cockpit` — single read endpoint
- `ingest/yazio/` — Python CLI orchestrator + daily GH Actions cron
- `ingest/withings/` — OAuth + measurements + activity + 3h cron
- `ingest/snapshot/` — aggregator that projects raw tables → `cockpit_snapshot.payload`
- `android/` — Kotlin Compose app reading Health Connect, posting to Edge Function
- `supabase/` — SQL migrations
- `mockup/` — original static HTML + Playwright harness
- `PRODUCT.md` / `DESIGN.md` / `ALGO_BIO_AGE.md` / `ALGO_SIGNAUX.md` — strategic + methodology

## Live ingestion

| Source | Mechanism | Cron |
|---|---|---|
| Yazio | `yazio-exporter` CLI in GH Actions, upserts to `yazio_day` / `yazio_meal` / `yazio_micronutrient_daily` | daily 04:00 UTC |
| Withings | OAuth, `getmeas` + `getactivity`, upserts to `withings_measurement` / `withings_activity_daily` / `withings_oauth` | every 3h |
| Health Connect | Android companion app `com.yannis.cockpitsync`, periodic 6h, posts to Edge Function `ingest-healthconnect`, upserts to `hc_raw_record` | every 6h |
| Snapshot | `ingest/snapshot/build_snapshot.py`, projects all raw tables into `cockpit_snapshot.payload` | hourly + after every ingest |

## Android APK

The companion app is built by `.github/workflows/android-apk.yml` on every change to `android/`. Download the latest artifact from the [Actions tab](https://github.com/gogetenk/yannis-cockpit/actions/workflows/android-apk.yml) → click the latest successful run → `cockpit-sync-debug-apk`.

To install:

1. Transfer the APK to the phone (USB, Drive, Telegram…).
2. Allow "Install unknown apps" for the file-manager app you use.
3. Open the APK, tap Install.
4. Open **Cockpit Sync**, tap **Accorder les permissions Health Connect**, accept every record type.
5. Tap **Synchroniser maintenant** for the first push. Subsequent syncs run automatically every 6 hours via WorkManager.

The first sync pulls 90 days of history; later runs are incremental.

## Local development

```bash
npm install
npm run dev   # → http://localhost:3000
```

Without Supabase env vars, the API falls back to the frozen mock so the cockpit still renders. With real env vars (`NEXT_PUBLIC_SUPABASE_URL` + `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` or `SUPABASE_SERVICE_ROLE_KEY`), it reads the live snapshot.

## API

```
GET /api/cockpit
→ { "source": "supabase" | "mock", "data": CockpitSnapshot }
```

See `lib/types.ts` for the snapshot shape.

## Mockup harness

```bash
npm run shot   # writes mockup/shots/*.png
```
