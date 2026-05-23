package com.yannis.cockpitsync

import io.ktor.client.HttpClient
import io.ktor.client.engine.okhttp.OkHttp
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.request.headers
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.http.contentType
import io.ktor.serialization.kotlinx.json.json
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

private val json = Json { ignoreUnknownKeys = true; encodeDefaults = false }

private val http = HttpClient(OkHttp) {
    install(ContentNegotiation) { json(json) }
}

data class IngestResult(val ok: Boolean, val inserted: Int, val errorText: String? = null)

/**
 * POST a batch of HCRecord-shaped JSON objects to the Edge Function.
 * Splits into chunks so we never exceed Supabase's request size limit.
 */
suspend fun postBatch(records: List<JsonObject>, chunkSize: Int = 200): IngestResult {
    if (records.isEmpty()) return IngestResult(ok = true, inserted = 0)
    var total = 0
    for (chunk in records.chunked(chunkSize)) {
        val body = buildJsonObject { put("records", JsonArray(chunk)) }
        val resp = http.post(Config.INGEST_URL) {
            contentType(ContentType.Application.Json)
            headers { append("x-cockpit-secret", Config.INGEST_SECRET) }
            setBody(body)
        }
        if (resp.status != HttpStatusCode.OK) {
            val text = resp.bodyAsText()
            return IngestResult(ok = false, inserted = total, errorText = "HTTP ${resp.status.value}: ${text.take(300)}")
        }
        val out = json.parseToJsonElement(resp.bodyAsText()).jsonObject()
        total += (out["inserted"]?.let { runCatching { it.toString().trim('"').toInt() }.getOrNull() } ?: 0)
    }
    return IngestResult(ok = true, inserted = total)
}

private fun kotlinx.serialization.json.JsonElement.jsonObject(): JsonObject =
    this as JsonObject
