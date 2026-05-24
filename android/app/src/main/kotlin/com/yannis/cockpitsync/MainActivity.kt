package com.yannis.cockpitsync

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
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
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import java.time.Instant
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

    // Live observation of DataStore — UI auto-refreshes when SyncWorker writes.
    val lastSync by settings.lastSyncFlow.collectAsState(initial = null)
    val lastStatus by settings.lastStatusFlow.collectAsState(initial = null)
    val lastInserted by settings.lastInsertedFlow.collectAsState(initial = 0L)

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

    // Observe the OneTime work to know when sync finishes and clear `syncing` flag.
    LaunchedEffect(syncing) {
        if (!syncing) return@LaunchedEffect
        // Simple timeout — actual completion will refresh lastSync via the Flow above.
        kotlinx.coroutines.delay(15_000)
        syncing = false
    }

    Scaffold(topBar = { TopAppBar(title = { Text("Cockpit Sync") }) }) { pad ->
        Column(Modifier.padding(pad).padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Statut : $status", style = MaterialTheme.typography.bodyLarge)
            lastSync?.let { Text("Dernier sync : ${fmt(it)}") }
            lastStatus?.let { Text("Résultat : $it") }
            if (lastInserted > 0L) Text("Inserts cumulés : $lastInserted")

            if (repo.status() == HealthRepo.Status.AVAILABLE && !permsOk) {
                Button(onClick = { permLauncher.launch(Permissions.PERMISSIONS) }) {
                    Text("Accorder les permissions Health Connect")
                }
            }
            if (permsOk) {
                Button(
                    enabled = !syncing,
                    onClick = {
                        scope.launch {
                            syncing = true
                            SyncWorker.runOnce(ctx)
                        }
                    }
                ) { Text(if (syncing) "Synchronisation…" else "Synchroniser maintenant") }
            }

            Spacer(Modifier.height(20.dp))
            Text(
                "Endpoint : ${Config.INGEST_URL.substringAfterLast('/')}",
                style = MaterialTheme.typography.labelMedium,
            )
            Text(
                "Fenêtre périodique : toutes les ${Config.SYNC_INTERVAL_MINUTES / 60} h " +
                    "(retry auto via WorkManager)",
                style = MaterialTheme.typography.labelMedium,
            )
        }
    }
}

private fun fmt(i: Instant): String =
    DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
        .withZone(ZoneId.systemDefault())
        .format(i)
