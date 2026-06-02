package com.yannis.cockpitsync

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.WorkerParameters
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.Constraints
import androidx.work.WorkManager
import java.time.Duration
import java.time.Instant

class SyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    private val tag = "SyncWorker"

    override suspend fun doWork(): Result {
        val repo = HealthRepo(applicationContext)
        val settings = Settings(applicationContext)

        if (repo.status() != HealthRepo.Status.AVAILABLE) {
            settings.setLastStatus("Health Connect not available")
            return Result.success()
        }
        if (!repo.hasAllPermissions()) {
            settings.setLastStatus("Permissions not granted, open app")
            return Result.success()
        }

        val now = Instant.now()
        // Force-resync input overrides the last-sync cursor. Used by the UI's
        // "Re-push N derniers jours" buttons to recover from upstream gaps.
        val forceDays = inputData.getLong(KEY_FORCE_SINCE_DAYS, -1L)
        val from = if (forceDays > 0) {
            now.minus(Duration.ofDays(forceDays))
        } else {
            settings.lastSync() ?: now.minus(Duration.ofDays(Config.INITIAL_BACKFILL_DAYS))
        }
        Log.i(tag, "reading HC window $from → $now (forceDays=$forceDays)")

        val records = try {
            repo.readWindow(from, now)
        } catch (e: Exception) {
            Log.e(tag, "HC read failed", e)
            settings.setLastStatus("HC read failed: ${e.message}")
            settings.setLastError("HC read: ${e.javaClass.simpleName} — ${e.message}")
            return Result.retry()
        }
        Log.i(tag, "${records.size} records to push")
        settings.setLastRecordsFound(records.size.toLong())

        // Per-day count summary for the diagnostic UI (record_type aggregated).
        // Keyed by local YYYY-MM-DD so the UI can render a "last 7 days" table.
        val byDay = HashMap<String, Long>()
        for (rec in records) {
            val startStr = rec["start_ts"]?.toString()?.trim('"') ?: continue
            try {
                val day = Instant.parse(startStr).atZone(java.time.ZoneId.systemDefault()).toLocalDate().toString()
                byDay[day] = (byDay[day] ?: 0) + 1
            } catch (_: Exception) {}
        }
        val byDayJson = byDay.entries.sortedByDescending { it.key }
            .joinToString(prefix = "{", postfix = "}") { (k, v) -> "\"$k\":$v" }
        settings.setLastByDayJson(byDayJson)

        val res = postBatch(records)
        if (!res.ok) {
            settings.setLastStatus("upload failed: ${res.errorText}")
            settings.setLastError("HTTP: ${res.errorText}")
            return Result.retry()
        }
        // Only advance the cursor on incremental syncs; forced re-pushes
        // shouldn't move the watermark forward (they're for repair).
        if (forceDays <= 0) settings.setLastSync(now)
        settings.setLastInserted(res.inserted.toLong())
        settings.setLastStatus("ok · ${res.inserted} insérés (${records.size} lus)")
        settings.setLastError(null)
        Log.i(tag, "synced. ${res.inserted} inserted server-side")
        return Result.success()
    }

    companion object {
        const val UNIQUE_NAME = "cockpit-sync-periodic"
        const val KEY_FORCE_SINCE_DAYS = "force_since_days"

        fun schedule(ctx: Context) {
            val req = PeriodicWorkRequestBuilder<SyncWorker>(Duration.ofMinutes(Config.SYNC_INTERVAL_MINUTES))
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build()
                )
                .build()
            WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(
                UNIQUE_NAME, ExistingPeriodicWorkPolicy.KEEP, req,
            )
        }

        fun runOnce(ctx: Context, forceSinceDays: Long = 0L) {
            val builder = androidx.work.OneTimeWorkRequestBuilder<SyncWorker>()
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build()
                )
            if (forceSinceDays > 0) {
                builder.setInputData(
                    androidx.work.Data.Builder()
                        .putLong(KEY_FORCE_SINCE_DAYS, forceSinceDays)
                        .build()
                )
            }
            WorkManager.getInstance(ctx).enqueue(builder.build())
        }
    }
}
