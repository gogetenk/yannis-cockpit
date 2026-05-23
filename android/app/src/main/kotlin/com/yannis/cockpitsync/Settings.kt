package com.yannis.cockpitsync

import android.content.Context
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import java.time.Instant

private val Context.dataStore by preferencesDataStore(name = "cockpit_sync_prefs")

private val LAST_SYNC = longPreferencesKey("last_sync_epoch")
private val LAST_STATUS = stringPreferencesKey("last_status")
private val LAST_INSERTED = longPreferencesKey("last_inserted")

class Settings(private val ctx: Context) {
    suspend fun lastSync(): Instant? {
        val sec = ctx.dataStore.data.map { it[LAST_SYNC] }.first()
        return sec?.let { Instant.ofEpochSecond(it) }
    }
    suspend fun setLastSync(t: Instant) {
        ctx.dataStore.edit { it[LAST_SYNC] = t.epochSecond }
    }
    suspend fun lastStatus(): String? = ctx.dataStore.data.map { it[LAST_STATUS] }.first()
    suspend fun setLastStatus(s: String) { ctx.dataStore.edit { it[LAST_STATUS] = s } }
    suspend fun lastInserted(): Long = ctx.dataStore.data.map { it[LAST_INSERTED] ?: 0L }.first()
    suspend fun setLastInserted(n: Long) { ctx.dataStore.edit { it[LAST_INSERTED] = n } }
}
