# AI Brief — Insights input contract

Status: **spec only**. The wiring inside `ingest/snapshot/build_snapshot.py`
will land in a separate commit to avoid conflicting with the parallel agent
currently editing the detector pipeline.

## Why

`build_ai_brief()` today builds the model prompt from a flat `facts` dict
derived ad-hoc from the snapshot payload (weight delta, dose, signaux watch
count, etc.). The dict is shaped for "what the screen shows", not "what
matters this week". As soon as the detector pipeline lands (`insight` table,
Famille 1/4/5), we have structured, scored, context-rich items that should
become the *primary* qualitative input — `facts` stays as the numeric ground
truth.

## Contract

The brief builder MUST read the top 3 *active* insights ordered by
`score DESC` from the `insight` table — same query as `/api/insights`
limited to 3 — and pass them to the model in addition to the existing
`facts` dict.

Field selection (only these — no PII, no raw metric arrays):

| field        | type                  | use                                |
|--------------|-----------------------|------------------------------------|
| `title`      | string                | one-line headline                  |
| `body`       | string                | short qualifier (\~1 sentence)     |
| `family`     | 1 \| 4 \| 5           | grouping signal                    |
| `severity`   | info\|watch\|alert    | tone hint                          |
| `data`       | jsonb                 | small numeric context (deltas, dates) |

`metric_keys`, `link_href`, `detector_key`, `id`, `detected_at`,
`score` are NOT passed to the model — they are routing metadata only.

## Combined payload example

```json
{
  "facts": {
    "today": "2026-05-25",
    "weight_kg": 78.4,
    "weight_delta_vs_ideal_kg": -0.6,
    "status_hero": "conforme",
    "dose_mg": 1.7,
    "signals_watch_count": 1
  },
  "insights": [
    {
      "title": "HRV en repli sur 14 j",
      "body": "Médiane nuit −7 ms vs baseline 90 j, sommeil stable.",
      "family": 4,
      "severity": "watch",
      "data": {"hrv_med_14d": 38, "hrv_med_90d": 45, "delta_ms": -7}
    },
    {
      "title": "Apport protéique sous-cible",
      "body": "7 j sous 1,4 g/kg, perte LBM possible si maintenu.",
      "family": 1,
      "severity": "watch",
      "data": {"prot_g_kg_7d": 1.21, "target_g_kg": 1.4}
    },
    {
      "title": "VO2max estimé +1,2 ml/kg/min",
      "body": "Tendance 30 j positive, cohérente avec Z2 hebdo.",
      "family": 5,
      "severity": "info",
      "data": {"vo2_30d": 47.8, "vo2_prev": 46.6}
    }
  ]
}
```

## Authoring rule for the model

Append to the existing system prompt:

> Tu peux citer ou contextualiser un insight reçu (champ `insights[]`) en
> t'appuyant sur son `title`, son `body` et son `data`. Tu ne dois **pas**
> inventer de nouvelle corrélation entre métriques absente des insights
> fournis : si la corrélation n'est pas dans `insights[].data` ou dans
> `facts`, ne la formule pas. Priorise les `severity = "alert"`, puis
> `"watch"`, puis `"info"`.

## Fallback

If the `insight` table query returns 0 rows (cold start, detectors not
populated yet), build the brief from `facts` alone — exact current
behaviour. No degraded prompt, no "no insights available" message.

## Caching

Same TTL as the rest of the snapshot. The brief is regenerated on each
snapshot rebuild; insights are read fresh at that moment (no separate
cache layer needed — the table is small and already indexed on `active`
+ `score`).
