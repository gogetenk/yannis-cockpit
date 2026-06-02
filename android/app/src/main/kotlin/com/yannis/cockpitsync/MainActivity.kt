package com.yannis.cockpitsync

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.health.connect.client.PermissionController
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        SyncWorker.schedule(applicationContext)
        setContent {
            MaterialTheme {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    Screen()
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Screen() {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val repo = remember { HealthRepo(ctx) }
    val settings = remember { Settings(ctx) }
    var status by remember { mutableStateOf("loading…") }
    var permsOk by remember { mutableStateOf(false) }
    var syncing by remember { mutableStateOf(false) }

    val lastSync by settings.lastSyncFlow.collectAsState(initial = null)
    val lastStatus by settings.lastStatusFlow.collectAsState(initial = null)
    val lastInserted by settings.lastInsertedFlow.collectAsState(initial = 0L)
    val lastError by settings.lastErrorFlow.collectAsState(initial = null)
    val lastFound by settings.lastRecordsFoundFlow.collectAsState(initial = 0L)
    val byDayJson by settings.lastByDayJsonFlow.collectAsState(initial = null)
    var rawPreview by remember { mutableStateOf<List<String>?>(null) }
    var rawLoading by remember { mutableStateOf(false) }
    var rawDays by remember { mutableStateOf(3L) }

    val permLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        contract = PermissionController.createRequestPermissionResultContract()
    ) { granted ->
        permsOk = granted.containsAll(Permissions.PERMISSIONS)
        if (permsOk) SyncWorker.runOnce(ctx)
    }

    LaunchedEffect(Unit) {
        when (repo.status()) {
            HealthRepo.Status.NOT_INSTALLED -> status = "Installe Health Connect depuis le Play Store"
            HealthRepo.Status.NEEDS_UPDATE -> status = "Mets Health Connect à jour"
            HealthRepo.Status.AVAILABLE -> {
                permsOk = repo.hasAllPermissions()
                status = if (permsOk) "prêt" else "permissions à accorder"
            }
        }
    }

    LaunchedEffect(syncing) {
        if (!syncing) return@LaunchedEffect
        kotlinx.coroutines.delay(20_000)
        syncing = false
    }

    Scaffold(topBar = { TopAppBar(title = { Text("Cockpit Sync") }) }) { pad ->
        Column(
            Modifier.padding(pad).padding(20.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Text("Statut : $status", style = MaterialTheme.typography.bodyLarge)
            lastSync?.let { Text("Dernier sync : ${fmt(it)}") }
            lastStatus?.let { Text("Résultat : $it") }
            if (lastFound > 0L) Text("Records trouvés dans HC : $lastFound")
            if (lastInserted > 0L) Text("Inserts serveur cumulés : $lastInserted")
            lastError?.let {
                Card(
                    Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)
                ) {
                    Column(Modifier.padding(12.dp)) {
                        Text("⚠ Erreur :", style = MaterialTheme.typography.titleSmall)
                        Text(it, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }

            if (repo.status() == HealthRepo.Status.AVAILABLE && !permsOk) {
                Button(onClick = { permLauncher.launch(Permissions.PERMISSIONS) }) {
                    Text("Accorder les permissions Health Connect")
                }
            }
            if (permsOk) {
                Button(
                    enabled = !syncing,
                    onClick = {
                        scope.launch { syncing = true; SyncWorker.runOnce(ctx) }
                    },
                    modifier = Modifier.fillMaxWidth()
                ) { Text(if (syncing) "Synchronisation…" else "Synchroniser depuis le dernier point") }

                Text(
                    "Re-pousser une fenêtre (ne déplace pas le curseur) :",
                    style = MaterialTheme.typography.labelMedium,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                    listOf(3L, 7L, 30L).forEach { days ->
                        OutlinedButton(
                            enabled = !syncing,
                            onClick = {
                                scope.launch {
                                    syncing = true
                                    SyncWorker.runOnce(ctx, forceSinceDays = days)
                                }
                            },
                            modifier = Modifier.weight(1f)
                        ) { Text("${days}j") }
                    }
                }
            }

            // Diagnostic view: per-day counts from the last successful read.
            // Lets the user see whether HC itself has data for each day
            // (independent of whether the push succeeded).
            byDayJson?.let { json ->
                val days = parseByDayJson(json)
                if (days.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp)) {
                            Text(
                                "Records lus dans Health Connect (par jour, dernière lecture)",
                                style = MaterialTheme.typography.titleSmall,
                            )
                            Spacer(Modifier.height(6.dp))
                            // Show every day in the last 14 — including ZERO days so
                            // the user can spot upstream gaps at a glance.
                            val today = LocalDate.now()
                            (0..13).forEach { d ->
                                val date = today.minusDays(d.toLong()).toString()
                                val count = days[date] ?: 0L
                                val flag = if (count == 0L) "❌" else "✓"
                                Text(
                                    "$flag  $date  →  $count records",
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        }
                    }
                }
            }

            // Raw HC viewer — read directly from Health Connect on the device
            // (does NOT push anywhere). Use this to confirm whether the data
            // even exists in HC for a given day; if HC itself is empty, the
            // gap is upstream (Health Sync / Huawei / Withings) and our APK
            // has nothing to push.
            if (permsOk) {
                Spacer(Modifier.height(8.dp))
                Text("Aperçu brut Health Connect", style = MaterialTheme.typography.titleSmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                    listOf(1L, 3L, 7L).forEach { days ->
                        OutlinedButton(
                            enabled = !rawLoading,
                            onClick = {
                                rawDays = days
                                scope.launch {
                                    rawLoading = true
                                    rawPreview = try {
                                        val now = Instant.now()
                                        val from = now.minus(java.time.Duration.ofDays(days))
                                        val recs = repo.readWindow(from, now)
                                        // Build readable one-line summaries, newest first.
                                        recs.asSequence()
                                            .mapNotNull { rec ->
                                                val t = rec["start_ts"]?.toString()?.trim('"') ?: return@mapNotNull null
                                                val rt = rec["record_type"]?.toString()?.trim('"') ?: "?"
                                                val v = rec["value_num"]?.toString() ?: ""
                                                val u = rec["unit"]?.toString()?.trim('"') ?: ""
                                                val src = rec["source_app"]?.toString()?.trim('"')?.substringAfterLast('.') ?: "?"
                                                Triple(t, rt, "$v $u".trim() to src)
                                            }
                                            .sortedByDescending { it.first }
                                            .take(80)
                                            .map { (t, rt, vs) ->
                                                val (vu, src) = vs
                                                val short = t.substring(5, 16).replace('T', ' ')
                                                "$short  $rt  $vu  [$src]"
                                            }
                                            .toList()
                                    } catch (e: Exception) {
                                        listOf("ERREUR: ${e.javaClass.simpleName} — ${e.message}")
                                    }
                                    rawLoading = false
                                }
                            },
                            modifier = Modifier.weight(1f)
                        ) { Text("${days}j brut") }
                    }
                }
                rawPreview?.let { lines ->
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp)) {
                            Text(
                                "${lines.size} lignes (dernières ${rawDays}j, 80 max)",
                                style = MaterialTheme.typography.labelMedium,
                            )
                            Spacer(Modifier.height(6.dp))
                            if (lines.isEmpty()) {
                                Text("Aucun record dans HC sur cette fenêtre.", style = MaterialTheme.typography.bodySmall)
                            } else {
                                lines.forEach { line ->
                                    Text(line, style = MaterialTheme.typography.bodySmall)
                                }
                            }
                        }
                    }
                }
                if (rawLoading) Text("Lecture HC en cours…", style = MaterialTheme.typography.labelMedium)
            }

            Spacer(Modifier.height(20.dp))
            Text(
                "Endpoint : ${Config.INGEST_URL.substringAfterLast('/')}",
                style = MaterialTheme.typography.labelMedium,
            )
            Text(
                "Fenêtre périodique : toutes les ${Config.SYNC_INTERVAL_MINUTES / 60} h (retry auto via WorkManager)",
                style = MaterialTheme.typography.labelMedium,
            )
        }
    }
}

private fun fmt(i: Instant): String =
    DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
        .withZone(ZoneId.systemDefault())
        .format(i)

// Ultra-light parser for the "{\"YYYY-MM-DD\":n,…}" format written by SyncWorker.
// Avoiding kotlinx.serialization here to keep this trivially side-effect-free.
private fun parseByDayJson(s: String): Map<String, Long> {
    val out = HashMap<String, Long>()
    if (s.length < 4) return out
    val inner = s.trim().removePrefix("{").removeSuffix("}")
    if (inner.isBlank()) return out
    inner.split(',').forEach { pair ->
        val parts = pair.split(':')
        if (parts.size != 2) return@forEach
        val k = parts[0].trim().trim('"')
        val v = parts[1].trim().toLongOrNull() ?: return@forEach
        if (k.isNotBlank()) out[k] = v
    }
    return out
}
