package com.yannis.cockpitsync

import android.content.Context
import android.util.Log
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.records.ActiveCaloriesBurnedRecord
import androidx.health.connect.client.records.DistanceRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.HeartRateVariabilityRmssdRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.RespiratoryRateRecord
import androidx.health.connect.client.records.RestingHeartRateRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.TotalCaloriesBurnedRecord
import androidx.health.connect.client.records.Vo2MaxRecord
import androidx.health.connect.client.records.metadata.Metadata
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.time.Instant
import java.time.temporal.ChronoUnit
import kotlin.reflect.KClass

/**
 * Wraps the Health Connect SDK and converts every supported record type into
 * the flat HCRecord shape the Edge Function expects.
 *
 * Read strategy: windowed read between (lastSyncedAt, now). The Edge Function
 * upserts on (record_type, record_uid), so overlapping windows are harmless.
 */
class HealthRepo(private val ctx: Context) {

    enum class Status { NOT_INSTALLED, NEEDS_UPDATE, AVAILABLE }

    fun status(): Status = when (HealthConnectClient.getSdkStatus(ctx)) {
        HealthConnectClient.SDK_UNAVAILABLE -> Status.NOT_INSTALLED
        HealthConnectClient.SDK_UNAVAILABLE_PROVIDER_UPDATE_REQUIRED -> Status.NEEDS_UPDATE
        else -> Status.AVAILABLE
    }

    private val client by lazy { HealthConnectClient.getOrCreate(ctx) }

    suspend fun grantedPermissions(): Set<String> =
        client.permissionController.getGrantedPermissions()

    suspend fun hasAllPermissions(): Boolean =
        grantedPermissions().containsAll(Permissions.PERMISSIONS)

    suspend fun readWindow(start: Instant, end: Instant): List<JsonObject> {
        val out = mutableListOf<JsonObject>()
        val range = TimeRangeFilter.between(start, end)

        // SleepSessionRecord — duration in minutes + stage breakdown.
        safeRead(SleepSessionRecord::class, range) { r ->
            val minutes = ChronoUnit.MINUTES.between(r.startTime, r.endTime).toDouble()
            val stagesJson = JsonArray(r.stages.map { s ->
                buildJsonObject {
                    put("stage", s.stage)
                    put("start", s.startTime.toString())
                    put("end", s.endTime.toString())
                    put("minutes", ChronoUnit.MINUTES.between(s.startTime, s.endTime))
                }
            })
            out += mkRecord(
                type = "sleep_session",
                uid = r.metadata.id,
                startTs = r.startTime,
                endTs = r.endTime,
                valueNum = minutes,
                unit = "min",
                metadata = r.metadata,
                extra = mapOf(
                    "stages" to stagesJson,
                    "title" to (r.title?.let { JsonPrimitive(it) } ?: JsonNull),
                ),
            )
        }

        // HeartRateRecord — a session of samples. We flatten to 1 row per sample.
        safeRead(HeartRateRecord::class, range) { r ->
            r.samples.forEach { s ->
                out += mkRecord(
                    type = "heart_rate",
                    uid = "${r.metadata.id}:${s.time.epochSecond}",
                    startTs = s.time,
                    endTs = null,
                    valueNum = s.beatsPerMinute.toDouble(),
                    unit = "bpm",
                    metadata = r.metadata,
                    extra = mapOf("session_id" to JsonPrimitive(r.metadata.id)),
                )
            }
        }

        safeRead(HeartRateVariabilityRmssdRecord::class, range) { r ->
            out += mkRecord(
                "hrv_rmssd", r.metadata.id, r.time, null,
                r.heartRateVariabilityMillis, "ms", r.metadata,
            )
        }
        safeRead(RestingHeartRateRecord::class, range) { r ->
            out += mkRecord(
                "resting_heart_rate", r.metadata.id, r.time, null,
                r.beatsPerMinute.toDouble(), "bpm", r.metadata,
            )
        }
        safeRead(StepsRecord::class, range) { r ->
            out += mkRecord(
                "steps", r.metadata.id, r.startTime, r.endTime,
                r.count.toDouble(), "count", r.metadata,
            )
        }
        safeRead(DistanceRecord::class, range) { r ->
            out += mkRecord(
                "distance", r.metadata.id, r.startTime, r.endTime,
                r.distance.inMeters, "m", r.metadata,
            )
        }
        safeRead(TotalCaloriesBurnedRecord::class, range) { r ->
            out += mkRecord(
                "total_calories", r.metadata.id, r.startTime, r.endTime,
                r.energy.inKilocalories, "kcal", r.metadata,
            )
        }
        safeRead(ActiveCaloriesBurnedRecord::class, range) { r ->
            out += mkRecord(
                "active_calories", r.metadata.id, r.startTime, r.endTime,
                r.energy.inKilocalories, "kcal", r.metadata,
            )
        }
        safeRead(ExerciseSessionRecord::class, range) { r ->
            out += mkRecord(
                "exercise_session", r.metadata.id, r.startTime, r.endTime,
                ChronoUnit.MINUTES.between(r.startTime, r.endTime).toDouble(),
                "min", r.metadata,
                extra = mapOf(
                    "exercise_type" to JsonPrimitive(r.exerciseType),
                    "title" to (r.title?.let { JsonPrimitive(it) } ?: JsonNull),
                ),
            )
        }
        safeRead(Vo2MaxRecord::class, range) { r ->
            out += mkRecord(
                "vo2_max", r.metadata.id, r.time, null,
                r.vo2MillilitersPerMinuteKilogram, "ml/kg/min", r.metadata,
            )
        }
        safeRead(OxygenSaturationRecord::class, range) { r ->
            out += mkRecord(
                "spo2", r.metadata.id, r.time, null,
                r.percentage.value, "%", r.metadata,
            )
        }
        safeRead(RespiratoryRateRecord::class, range) { r ->
            out += mkRecord(
                "respiratory_rate", r.metadata.id, r.time, null,
                r.rate, "rpm", r.metadata,
            )
        }

        return out
    }

    private suspend fun <T : androidx.health.connect.client.records.Record> safeRead(
        klass: KClass<T>,
        range: TimeRangeFilter,
        block: (T) -> Unit,
    ) {
        try {
            val resp = client.readRecords(ReadRecordsRequest(klass, range))
            resp.records.forEach(block)
        } catch (e: SecurityException) {
            Log.w("HealthRepo", "missing permission for ${klass.simpleName}", e)
        } catch (e: Exception) {
            Log.w("HealthRepo", "read failed for ${klass.simpleName}: ${e.message}")
        }
    }

    private fun mkRecord(
        type: String,
        uid: String,
        startTs: Instant,
        endTs: Instant?,
        valueNum: Double?,
        unit: String?,
        metadata: Metadata,
        extra: Map<String, JsonElement> = emptyMap(),
    ): JsonObject = buildJsonObject {
        put("record_type", type)
        put("record_uid", uid)
        put("start_ts", startTs.toString())
        if (endTs != null) put("end_ts", endTs.toString())
        if (valueNum != null) put("value_num", valueNum)
        if (unit != null) put("unit", unit)
        put("source_app", metadata.dataOrigin.packageName)
        metadata.device?.let {
            val label = listOfNotNull(it.manufacturer, it.model).joinToString(" ").ifBlank { null }
            if (label != null) put("source_device", label)
        }
        if (extra.isNotEmpty()) {
            put("payload", buildJsonObject {
                extra.forEach { (k, v) -> put(k, v) }
            })
        }
    }
}
