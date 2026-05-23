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
        val from = settings.lastSync() ?: now.minus(Duration.ofDays(Config.INITIAL_BACKFILL_DAYS))
        Log.i(tag, "reading HC window $from → $now")

        val records = try {
            repo.readWindow(from, now)
        } catch (e: Exception) {
            Log.e(tag, "HC read failed", e)
            settings.setLastStatus("HC read failed: ${e.message}")
            return Result.retry()
        }
        Log.i(tag, "${records.size} records to push")

        val res = postBatch(records)
        if (!res.ok) {
            settings.setLastStatus("upload failed: ${res.errorText}")
            return Result.retry()
        }
        settings.setLastSync(now)
        settings.setLastInserted(res.inserted.toLong())
        settings.setLastStatus("ok · ${res.inserted} insérés")
        Log.i(tag, "synced. ${res.inserted} inserted server-side")
        return Result.success()
    }

    companion object {
        const val UNIQUE_NAME = "cockpit-sync-periodic"

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

        fun runOnce(ctx: Context) {
            val req = androidx.work.OneTimeWorkRequestBuilder<SyncWorker>()
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build()
                )
                .build()
            WorkManager.getInstance(ctx).enqueue(req)
        }
    }
}
