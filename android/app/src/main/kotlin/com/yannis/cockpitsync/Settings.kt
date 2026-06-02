package com.yannis.cockpitsync

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import java.time.Instant

private val Context.dataStore by preferencesDataStore(name = "cockpit_sync_prefs")

private val LAST_SYNC = longPreferencesKey("last_sync_epoch")
private val LAST_STATUS = stringPreferencesKey("last_status")
private val LAST_INSERTED = longPreferencesKey("last_inserted")
private val LAST_ERROR = stringPreferencesKey("last_error")
private val LAST_RECORDS_FOUND = longPreferencesKey("last_records_found")
// JSON map "YYYY-MM-DD" → count of records found in HC at last sync.
private val LAST_BY_DAY_JSON = stringPreferencesKey("last_by_day_json")

class Settings(private val ctx: Context) {
    val lastSyncFlow: Flow<Instant?> =
        ctx.dataStore.data.map { p -> p[LAST_SYNC]?.let { Instant.ofEpochSecond(it) } }
    val lastStatusFlow: Flow<String?> =
        ctx.dataStore.data.map { it[LAST_STATUS] }
    val lastInsertedFlow: Flow<Long> =
        ctx.dataStore.data.map { it[LAST_INSERTED] ?: 0L }
    val lastErrorFlow: Flow<String?> =
        ctx.dataStore.data.map { it[LAST_ERROR] }
    val lastRecordsFoundFlow: Flow<Long> =
        ctx.dataStore.data.map { it[LAST_RECORDS_FOUND] ?: 0L }
    val lastByDayJsonFlow: Flow<String?> =
        ctx.dataStore.data.map { it[LAST_BY_DAY_JSON] }

    suspend fun lastSync(): Instant? = lastSyncFlow.first()
    suspend fun setLastSync(t: Instant) {
        ctx.dataStore.edit { it[LAST_SYNC] = t.epochSecond }
    }
    suspend fun setLastStatus(s: String) { ctx.dataStore.edit { it[LAST_STATUS] = s } }
    suspend fun setLastInserted(n: Long) { ctx.dataStore.edit { it[LAST_INSERTED] = n } }
    suspend fun setLastError(s: String?) {
        ctx.dataStore.edit { p -> if (s == null) p.remove(LAST_ERROR) else p[LAST_ERROR] = s }
    }
    suspend fun setLastRecordsFound(n: Long) {
        ctx.dataStore.edit { it[LAST_RECORDS_FOUND] = n }
    }
    suspend fun setLastByDayJson(s: String) {
        ctx.dataStore.edit { it[LAST_BY_DAY_JSON] = s }
    }
}
