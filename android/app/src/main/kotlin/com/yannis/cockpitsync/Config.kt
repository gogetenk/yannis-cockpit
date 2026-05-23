package com.yannis.cockpitsync

// Hardcoded for this single-user private deployment. To rotate the secret,
// update both this file and the COCKPIT_INGEST_SECRET env var on the
// Supabase Edge Function side, then rebuild the APK.
object Config {
    const val INGEST_URL = "https://rfigopnkrjxrdwsoggpt.supabase.co/functions/v1/ingest-healthconnect"
    const val INGEST_SECRET = "3m1ayc98_3j3WoWepxei76EFpYpO9HadgCS0v84lr1c"

    // How far back to look on the very first sync (no token yet).
    const val INITIAL_BACKFILL_DAYS = 90L

    // Periodic sync cadence. Android caps periodic WorkRequest at 15 min min.
    const val SYNC_INTERVAL_MINUTES = 6L * 60
}
