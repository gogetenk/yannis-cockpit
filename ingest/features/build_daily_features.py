"""
Build daily_features: one row per day aggregating Yazio + Withings + Huawei
+ Wegovy into a flat feature vector, plus rolling intra-individual z-scores.

Reads:
  yazio_day                    daily intake totals
  yazio_micronutrient_daily    sodium/alcohol/saturated/fiber/sugar (when present)
  withings_measurement         weight, body fat, muscle, SBP/DBP, HR
  withings_activity_daily      steps, active_kcal, total_kcal, active_min
  huawei_daily                 rest HR, avg HR, sleep stages
  wegovy_injection             dose log (carry-forward)

Writes:
  daily_features (date PK)

CLI:
  python build_daily_features.py [--since YYYY-MM-DD]

Default --since recomputes the last 120 days. Full backfill: --since 2017-01-01.
The script is idempotent — re-running any window upserts in place.

Env:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

# Sanitization layer lives in ingest/yazio. Add the project root to sys.path
# so this script (run from ingest/features/) can import it without packaging.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ingest.yazio import enrich_estimation, llm_sanity, sanitize  # noqa: E402

try:
    from ingest.yazio.fetch_food_items import fetch_food_items as _fetch_food_items
except Exception:  # pragma: no cover - keeps build_daily_features importable
    _fetch_food_items = None  # type: ignore[assignment]


# Cache of food_items per date so a day with multiple corrections fetches once.
_FOOD_ITEMS_CACHE: dict[str, list[dict] | None] = {}


def _get_food_items_for_date(d_iso: str) -> list[dict] | None:
    """Best-effort fetch of Yazio food items for a date. Never raises.

    Returns None when:
      - YAZIO_EMAIL/YAZIO_PASSWORD not set (e.g. local dev)
      - fetch helper unavailable
      - the Yazio API call fails (rate-limit / timeout / parse error)
    """
    if d_iso in _FOOD_ITEMS_CACHE:
        return _FOOD_ITEMS_CACHE[d_iso]
    if _fetch_food_items is None:
        _FOOD_ITEMS_CACHE[d_iso] = None
        return None
    if not (os.environ.get("YAZIO_EMAIL") and os.environ.get("YAZIO_PASSWORD")):
        _FOOD_ITEMS_CACHE[d_iso] = None
        return None
    try:
        items = _fetch_food_items(d_iso)
    except Exception as e:
        print(
            f"[features] fetch_food_items({d_iso}) failed: {e} -- LLM will run "
            "with food_items=None",
            file=sys.stderr,
        )
        items = None
    _FOOD_ITEMS_CACHE[d_iso] = items
    return items


def _load_active_llm_reviews() -> dict[tuple[str, str], float | None]:
    """Map (date_iso, nutrient_id) -> active LLM-reviewed sanitized_value.

    Used so build_daily_features stays consistent with the canonical LLM
    decision already persisted in `yazio_correction`. Re-calling the LLM on
    every cron tick would yield slightly different numbers (Haiku is not
    deterministic) and overwrite the row stored value.
    """
    try:
        rows = sb_get(
            "yazio_correction",
            {
                "select": "date,nutrient_id,rule_key,sanitized_value",
                "source": "eq.llm",
                "reverted_at": "is.null",
                "rule_key": "like.llm_review_*",
            },
        )
    except Exception as e:
        print(f"[features] could not load active LLM reviews: {e}", file=sys.stderr)
        return {}
    out: dict[tuple[str, str], float | None] = {}
    for r in rows:
        d = str(r.get("date") or "")[:10]
        nid = r.get("nutrient_id") or ""
        sv = r.get("sanitized_value")
        try:
            out[(d, nid)] = float(sv) if sv is not None else None
        except (TypeError, ValueError):
            out[(d, nid)] = None
    return out


def escalate_corrections_to_llm(
    corrections: list[sanitize.Correction],
    daily_kcal_by_date: dict[str, float | None],
) -> tuple[list[sanitize.Correction], dict[str, dict[str, float | None]]]:
    """Run the LLM second-opinion on every rule-fired correction.

    Returns the post-LLM correction list AND a dict
    ``{date_iso: {nutrient_id: refined_value_or_None}}`` so the caller can
    patch the corresponding daily_features rows in place.

    Safe to call with ANTHROPIC_API_KEY unset or anthropic SDK missing:
    `llm_sanity.review_correction` short-circuits and returns the input.

    Optimization: if an active `llm_review_*` correction already exists for
    (date, nutrient), reuse its sanitized_value instead of re-calling the
    LLM (which is non-deterministic).
    """
    if not corrections:
        return corrections, {}

    existing_reviews = _load_active_llm_reviews()

    overrides: dict[str, dict[str, float | None]] = {}
    out: list[sanitize.Correction] = []
    for c in corrections:
        if c.source != "rule":
            out.append(c)
            continue
        prior = existing_reviews.get((c.date, c.nutrient_id))
        if (c.date, c.nutrient_id) in existing_reviews:
            # Reuse the canonical LLM verdict on file.
            overrides.setdefault(c.date, {})[c.nutrient_id] = prior
            # Drop this rule correction from the list -- it's shadowed by
            # the existing LLM row, persist_corrections will skip it anyway.
            continue
        food_items = _get_food_items_for_date(c.date)
        daily_kcal = daily_kcal_by_date.get(c.date)
        try:
            reviewed = llm_sanity.review_correction(c, food_items, daily_kcal)
        except Exception as e:  # belt-and-braces: never block the pipeline
            print(
                f"[features] llm_sanity.review_correction failed for "
                f"{c.date}/{c.nutrient_id}: {e}",
                file=sys.stderr,
            )
            reviewed = c
        out.append(reviewed)
        if (
            reviewed.source == "llm"
            and reviewed.sanitized_value != c.sanitized_value
        ):
            overrides.setdefault(c.date, {})[c.nutrient_id] = reviewed.sanitized_value
    return out, overrides


def _apply_llm_overrides_to_rows(
    rows: list[dict],
    overrides: dict[str, dict[str, float | None]],
) -> None:
    """Patch sanitized fields on the daily_features rows in place."""
    if not overrides:
        return
    field_by_nutrient = {
        sanitize.NUT_ALCOHOL: "alcohol_g",
        sanitize.NUT_SODIUM: "sodium_mg",
        sanitize.NUT_FAT_SAT: "fat_sat_g",
        sanitize.NUT_SUGAR: "sugar_g",
        sanitize.NUT_FIBER: "fiber_g",
    }
    by_date = {r["date"]: r for r in rows}
    for d_iso, nut_map in overrides.items():
        row = by_date.get(d_iso)
        if not row:
            continue
        for nutrient_id, refined in nut_map.items():
            field = field_by_nutrient.get(nutrient_id)
            if field is None:
                continue
            row[field] = refined
            # Provenance: mark the field as LLM-reviewed (sanitize escalation).
            source_field = f"{field}_source"
            if source_field in row or refined is not None:
                row[source_field] = "llm_review" if refined is not None else None
            # Re-derive pct_e_sat if saturated fat changed.
            if field == "fat_sat_g" and row.get("kcal"):
                kcal = row.get("kcal")
                if refined is None or kcal is None or kcal <= 0:
                    row["pct_e_sat"] = None
                else:
                    row["pct_e_sat"] = round((refined * 9.0) / kcal * 100.0, 2)


# ---------- nutrient_id detection ---------------------------------------
# yazio_micronutrient_daily only exposes minerals/vitamins today. We still
# look up the canonical IDs here so the populator picks them up automatically
# the day the upstream ingest learns to write them.
SODIUM_IDS = {"mineral.sodium", "sodium", "minerals.sodium", "nutrient.sodium"}
ALCOHOL_IDS = {"alcohol", "nutrient.alcohol", "macro.alcohol"}
FAT_SAT_IDS = {
    "fat.saturated",
    "saturated_fat",
    "nutrient.fat_saturated",
    "nutrient.saturated",
    "macro.fat_saturated",
}
FAT_PUFA_IDS = {
    "fat.polyunsaturated",
    "nutrient.fat_polyunsaturated",
    "nutrient.polyunsaturated",
    "fat.pufa",
}
FAT_MUFA_IDS = {
    "fat.monounsaturated",
    "nutrient.fat_monounsaturated",
    "nutrient.monounsaturated",
    "fat.mufa",
}
SUGAR_IDS = {"sugar", "nutrient.sugar", "carb.sugar"}
FIBER_IDS = {"fiber", "nutrient.fiber", "nutrient.dietaryfiber", "carb.fiber"}

EPS = 1e-6


# ---------- supabase REST helpers ---------------------------------------

def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"missing env var: {name}")
    return v


def sb_headers() -> dict:
    key = env("SUPABASE_SERVICE_ROLE_KEY")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def sb_get(path: str, params: dict | None = None) -> list:
    out: list = []
    page_size = 1000
    offset = 0
    while True:
        headers = sb_headers()
        headers["Range-Unit"] = "items"
        headers["Range"] = f"{offset}-{offset + page_size - 1}"
        r = requests.get(
            f"{env('SUPABASE_URL')}/rest/v1/{path}",
            headers=headers,
            params=params,
            timeout=60,
        )
        r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return out


def sb_upsert(rows: list[dict], table: str, on_conflict: str, batch_size: int = 500) -> int:
    if not rows:
        return 0
    n = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        url = f"{env('SUPABASE_URL')}/rest/v1/{table}?on_conflict={on_conflict}"
        headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"}
        r = requests.post(url, headers=headers, data=json.dumps(chunk), timeout=60)
        if not r.ok:
            sys.exit(f"upsert {table} failed {r.status_code}: {r.text[:500]}")
        n += len(chunk)
    return n


# ---------- loaders ------------------------------------------------------

def load_yazio_day() -> dict[date, dict]:
    rows = sb_get("yazio_day", {"select": "date,kcal,protein_g,carb_g,fat_g,steps,activity_kcal,weight_kg,body_fat_pct"})
    out: dict[date, dict] = {}
    for r in rows:
        d = date.fromisoformat(r["date"])
        out[d] = r
    return out


def load_yazio_meals() -> dict[date, list[dict]]:
    """Per-date list of meal rows (kcal + macros + meal slot name).

    Used by `enrich_estimation` to give the LLM enough context to estimate
    missing micronutrients (fat_sat/sodium/sugar/fiber/alcohol) on days
    where Yazio's photo-AI logger only returned macros.
    """
    rows = sb_get(
        "yazio_meal",
        {"select": "date,meal,kcal,protein_g,carb_g,fat_g"},
    )
    out: dict[date, list[dict]] = defaultdict(list)
    for r in rows:
        try:
            d = date.fromisoformat(r["date"])
        except (TypeError, ValueError):
            continue
        out[d].append(
            {
                "name": r.get("meal"),
                "meal": r.get("meal"),
                "kcal": r.get("kcal"),
                "protein_g": r.get("protein_g"),
                "carb_g": r.get("carb_g"),
                "fat_g": r.get("fat_g"),
            }
        )
    return out


def load_yazio_food_items() -> dict[date, list[dict]]:
    """Per-date list of consumed food items (rich named ingredients).

    Sourced from `yazio_food_item_daily` (Chantier 1). Empty list for a date
    means "no items persisted yet" -- the enrichment layer falls back to
    yazio_meal-level totals in that case.
    """
    rows = sb_get(
        "yazio_food_item_daily",
        {
            "select": (
                "date,meal_slot,item_index,item_name,amount_g,is_ai_estimate,"
                "source_kind,kcal_per_100g,protein_per_100g,carb_per_100g,fat_per_100g"
            )
        },
    )
    out: dict[date, list[dict]] = defaultdict(list)
    for r in rows:
        try:
            d = date.fromisoformat(r["date"])
        except (TypeError, ValueError):
            continue
        out[d].append(
            {
                "meal": r.get("meal_slot"),
                "meal_slot": r.get("meal_slot"),
                "item_index": r.get("item_index"),
                "name": r.get("item_name"),
                "amount_g": r.get("amount_g"),
                "is_ai_estimate": bool(r.get("is_ai_estimate")),
                "source_kind": r.get("source_kind") or "product",
                "kcal_per_100g": r.get("kcal_per_100g"),
                "protein_per_100g": r.get("protein_per_100g"),
                "carb_per_100g": r.get("carb_per_100g"),
                "fat_per_100g": r.get("fat_per_100g"),
            }
        )
    # Stable order: by meal slot then item_index so the LLM sees the same
    # narrative each time.
    for d in out:
        out[d].sort(
            key=lambda x: (x.get("meal_slot") or "", x.get("item_index") or 0)
        )
    return out


def load_yazio_micros() -> dict[date, dict[str, float]]:
    rows = sb_get("yazio_micronutrient_daily", {"select": "date,nutrient_id,value"})
    out: dict[date, dict[str, float]] = defaultdict(dict)
    for r in rows:
        d = date.fromisoformat(r["date"])
        try:
            out[d][r["nutrient_id"]] = float(r["value"])
        except (TypeError, ValueError):
            continue
    return out


def _pick(micros: dict[str, float], ids: set[str]) -> float | None:
    for k, v in micros.items():
        if k in ids:
            return v
    # fuzzy: substring fallback
    needle = next(iter(ids)).split(".")[-1]
    for k, v in micros.items():
        if needle in k.lower():
            return v
    return None


def load_withings_measurements() -> dict[date, dict[str, list[float]]]:
    """type_code: 1=weight, 6=fat_pct, 8=fat_mass, 9=DBP, 10=SBP, 11=HR, 76=muscle."""
    rows = sb_get(
        "withings_measurement",
        {"select": "ts,type_code,value,position", "type_code": "in.(1,6,9,10,11,76)"},
    )
    out: dict[date, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        # ignore segmental rows. Withings convention: position 0 (or NULL)
        # = whole-body / cuff reading; positions >= 2 are per-limb BIA.
        pos = r.get("position")
        if pos is not None and pos != 0:
            continue
        ts = r["ts"]
        d = date.fromisoformat(ts[:10])
        try:
            v = float(r["value"])
        except (TypeError, ValueError):
            continue
        tc = r["type_code"]
        key = {1: "weight", 6: "body_fat", 9: "dbp", 10: "sbp", 11: "hr", 76: "muscle"}.get(tc)
        if key:
            out[d][key].append(v)
    return out


def load_withings_activity() -> dict[date, dict]:
    rows = sb_get("withings_activity_daily", {"select": "date,steps,active_kcal,total_kcal,active_min"})
    out: dict[date, dict] = {}
    for r in rows:
        d = date.fromisoformat(r["date"])
        out[d] = r
    return out


def load_huawei() -> dict[date, dict]:
    rows = sb_get(
        "huawei_daily",
        {"select": "date,rest_hr_min,hr_continuous_avg,sleep_total_min,sleep_deep_min,sleep_rem_min"},
    )
    out: dict[date, dict] = {}
    for r in rows:
        d = date.fromisoformat(r["date"])
        out[d] = r
    return out


def load_healthconnect_sleep() -> dict[date, dict[str, int]]:
    """Aggregate Health Connect sleep_session rows -> {date: {sleep_total_min,...}}.

    Source: `hc_raw_record` populated by the Android cockpit-sync APK. We
    attribute each session to the LOCAL (Europe/Paris) date of `end_ts` —
    i.e. the morning the user woke up — so "sleep on day X" matches the
    daily_features row for X.

    Returns only `sleep_total_min` for now; Health Connect can expose
    stage records (deep/rem/light) as separate `sleep_session.stages[]`
    items in the payload, but Huawei watches surfaced via Health Connect
    typically don't fill those, so we leave deep/rem null here and let
    huawei_daily provide them when available.
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # py<3.9
        return {}
    paris = ZoneInfo("Europe/Paris")
    try:
        rows = sb_get(
            "hc_raw_record",
            {
                "select": "start_ts,end_ts",
                "record_type": "eq.sleep_session",
                "order": "end_ts.asc",
            },
        )
    except Exception as e:
        print(f"[features] load_healthconnect_sleep failed: {e}", file=sys.stderr)
        return {}
    by_date: dict[date, float] = defaultdict(float)
    for r in rows:
        s = r.get("start_ts")
        e = r.get("end_ts")
        if not s or not e:
            continue
        try:
            ts_s = datetime.fromisoformat(s.replace("Z", "+00:00"))
            ts_e = datetime.fromisoformat(e.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        dur_min = (ts_e - ts_s).total_seconds() / 60.0
        if dur_min <= 0 or dur_min > 24 * 60:
            continue
        wake_date = ts_e.astimezone(paris).date()
        by_date[wake_date] += dur_min
    out: dict[date, dict[str, int]] = {}
    for d, mins in by_date.items():
        # Cap at 16h to ignore overlapping/duplicate sessions from multiple
        # apps writing to Health Connect.
        out[d] = {"sleep_total_min": int(round(min(mins, 16 * 60)))}
    return out


def load_wegovy() -> list[tuple[date, float]]:
    rows = sb_get("wegovy_injection", {"select": "date,dose_mg", "order": "date.asc"})
    out: list[tuple[date, float]] = []
    for r in rows:
        d = date.fromisoformat(r["date"])
        out.append((d, float(r["dose_mg"])))
    return out


# ---------- merging primitives ------------------------------------------

def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _min(xs: list[float]) -> float | None:
    return min(xs) if xs else None


def _to_int(x: Any) -> int | None:
    if x is None:
        return None
    try:
        return int(round(float(x)))
    except (TypeError, ValueError):
        return None


def _to_num(x: Any) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def wegovy_state_for(d: date, ladder: list[tuple[date, float]]) -> tuple[float | None, int | None]:
    """Return (current_dose_mg, days_since_injection) using carry-forward."""
    dose = None
    last_inj = None
    for inj_d, mg in ladder:
        if inj_d <= d:
            dose = mg
            last_inj = inj_d
        else:
            break
    if last_inj is None:
        return None, None
    return dose, (d - last_inj).days


def build_row_raw(
    d: date,
    yz: dict | None,
    micros: dict[str, float],
    wm: dict[str, list[float]],
    wa: dict | None,
    hu: dict | None,
    wegovy_ladder: list[tuple[date, float]],
) -> dict:
    """Assemble the non-zscore portion of a daily_features row."""

    # intake
    kcal = _to_num(yz.get("kcal")) if yz else None
    protein_g = _to_num(yz.get("protein_g")) if yz else None
    carb_g = _to_num(yz.get("carb_g")) if yz else None
    fat_g = _to_num(yz.get("fat_g")) if yz else None
    # Logged-day flag: positive evidence the user recorded food on this date.
    # An implicit-zero day (Yazio API returns nothing, or every meal slot is 0)
    # is treated as "non loggé" — detectors / baselines must NOT count it as
    # "user ate 0 kcal" because that biases means downward.
    is_logged = bool(kcal is not None and kcal > 0)

    sodium_mg = _pick(micros, SODIUM_IDS)
    alcohol_g = _pick(micros, ALCOHOL_IDS)
    fat_sat_g = _pick(micros, FAT_SAT_IDS)
    fat_pufa_g = _pick(micros, FAT_PUFA_IDS)
    fat_mufa_g = _pick(micros, FAT_MUFA_IDS)
    sugar_g = _pick(micros, SUGAR_IDS)
    fiber_g = _pick(micros, FIBER_IDS)

    # Plausibility sanitization — delegated to ingest/yazio/sanitize.py.
    # Each drop emits a Correction record that the caller batches into the
    # `yazio_correction` audit table. Rules: alcohol hard cap, alcohol-kcal
    # coherence, sodium hard cap, sat>fat, sugar>carb, fiber>carb.
    raw_pack = {
        sanitize.NUT_ALCOHOL: alcohol_g,
        sanitize.NUT_SODIUM: sodium_mg,
        sanitize.NUT_FAT_SAT: fat_sat_g,
        sanitize.NUT_SUGAR: sugar_g,
        sanitize.NUT_FIBER: fiber_g,
    }
    # apply() ignores None entries.
    raw_pack_clean = {k: v for k, v in raw_pack.items() if v is not None}
    sanitized, day_corrections = sanitize.apply(d, kcal, fat_g, carb_g, raw_pack_clean)
    alcohol_g = sanitized.get(sanitize.NUT_ALCOHOL, alcohol_g)
    sodium_mg = sanitized.get(sanitize.NUT_SODIUM, sodium_mg)
    fat_sat_g = sanitized.get(sanitize.NUT_FAT_SAT, fat_sat_g)
    sugar_g = sanitized.get(sanitize.NUT_SUGAR, sugar_g)
    fiber_g = sanitized.get(sanitize.NUT_FIBER, fiber_g)
    # Stash corrections on the resulting row so the caller can collect them
    # without re-running the rules. Stripped before upsert.
    _corrections_for_row = day_corrections

    def pct_e(g: float | None) -> float | None:
        if g is None or kcal is None or kcal <= 0:
            return None
        return round((g * 9.0) / kcal * 100.0, 2)

    pct_e_sat = pct_e(fat_sat_g)
    pct_e_pufa = pct_e(fat_pufa_g)
    pct_e_mufa = pct_e(fat_mufa_g)

    # weight / composition: Withings priority, Yazio fallback
    w_weight = _mean(wm.get("weight", [])) if wm else None
    w_bfp = _mean(wm.get("body_fat", [])) if wm else None
    w_muscle = _mean(wm.get("muscle", [])) if wm else None

    weight_kg = w_weight if w_weight is not None else (_to_num(yz.get("weight_kg")) if yz else None)
    body_fat_pct = w_bfp if w_bfp is not None else (_to_num(yz.get("body_fat_pct")) if yz else None)
    muscle_kg = w_muscle

    # activity: Withings activity table priority, Yazio steps fallback
    steps = None
    active_kcal = None
    total_kcal = None
    active_min = None
    if wa:
        steps = _to_int(wa.get("steps"))
        active_kcal = _to_num(wa.get("active_kcal"))
        total_kcal = _to_num(wa.get("total_kcal"))
        active_min = _to_int(wa.get("active_min"))
    if steps is None and yz:
        steps = _to_int(yz.get("steps"))
    if active_kcal is None and yz:
        active_kcal = _to_num(yz.get("activity_kcal"))

    # cardio
    sbp = _mean(wm.get("sbp", [])) if wm else None
    dbp = _mean(wm.get("dbp", [])) if wm else None
    hr_rest_min = None
    hr_avg = None
    if hu:
        hr_rest_min = _to_num(hu.get("rest_hr_min"))
        hr_avg = _to_num(hu.get("hr_continuous_avg"))
    # Withings HR fallback: take the min of withings HR readings as proxy resting
    if hr_rest_min is None and wm and wm.get("hr"):
        hr_rest_min = _min(wm["hr"])
    if hr_avg is None and wm and wm.get("hr"):
        hr_avg = _mean(wm["hr"])

    # sleep
    sleep_total_min = _to_int(hu.get("sleep_total_min")) if hu else None
    sleep_deep_min = _to_int(hu.get("sleep_deep_min")) if hu else None
    sleep_rem_min = _to_int(hu.get("sleep_rem_min")) if hu else None

    # wegovy
    wegovy_dose_mg, wegovy_days_since_injection = wegovy_state_for(d, wegovy_ladder)

    # Provenance: any non-null micronutrient at this point comes from Yazio
    # (raw_pack_clean was sourced from yazio_micronutrient_daily). The LLM
    # estimator runs later and only fills slots that are still None here.
    fat_sat_g_source = "yazio" if fat_sat_g is not None else None
    sodium_mg_source = "yazio" if sodium_mg is not None else None
    sugar_g_source = "yazio" if sugar_g is not None else None
    fiber_g_source = "yazio" if fiber_g is not None else None
    alcohol_g_source = "yazio" if alcohol_g is not None else None

    return {
        "__corrections": _corrections_for_row,  # stripped before upsert
        "date": d.isoformat(),
        "is_logged": is_logged,
        "kcal": kcal,
        "protein_g": protein_g,
        "carb_g": carb_g,
        "fat_g": fat_g,
        "fat_sat_g": fat_sat_g,
        "fat_sat_g_source": fat_sat_g_source,
        "fiber_g": fiber_g,
        "fiber_g_source": fiber_g_source,
        "sodium_mg": sodium_mg,
        "sodium_mg_source": sodium_mg_source,
        "alcohol_g": alcohol_g,
        "alcohol_g_source": alcohol_g_source,
        "sugar_g": sugar_g,
        "sugar_g_source": sugar_g_source,
        "pct_e_sat": pct_e_sat,
        "pct_e_pufa": pct_e_pufa,
        "pct_e_mufa": pct_e_mufa,
        "weight_kg": weight_kg,
        "body_fat_pct": body_fat_pct,
        "muscle_kg": muscle_kg,
        "steps": steps,
        "active_kcal": active_kcal,
        "total_kcal": total_kcal,
        "active_min": active_min,
        "sbp": sbp,
        "dbp": dbp,
        "hr_rest_min": hr_rest_min,
        "hr_avg": hr_avg,
        "sleep_total_min": sleep_total_min,
        "sleep_deep_min": sleep_deep_min,
        "sleep_rem_min": sleep_rem_min,
        "wegovy_dose_mg": wegovy_dose_mg,
        "wegovy_days_since_injection": wegovy_days_since_injection,
    }


# ---------- LLM micronutrient estimation (fill missing Yazio micros) ----

_MICRO_FIELDS = ("fat_sat_g", "sodium_mg", "sugar_g", "fiber_g", "alcohol_g")


def enrich_rows_with_llm_estimates(
    rows: list[dict],
    meals_by_date: dict[date, list[dict]],
    since: date,
    *,
    force: bool = False,
    existing_sources_by_date: dict[str, dict[str, str | None]] | None = None,
    food_items_by_date: dict[date, list[dict]] | None = None,
) -> tuple[int, int]:
    """For each in-scope row missing any of the 5 micros, ask Haiku to fill them.

    Mutates rows in place. Returns (n_dates_processed, n_nutrients_filled).

    Skips dates where:
      - no meals were logged in yazio_meal (LLM has no anchor)
      - all 5 micros are already populated (yazio or prior llm_*)
      - `force=False` AND every missing field already has a non-null
        `_source` recorded in `existing_sources_by_date` (idempotent backrun)
    """
    n_dates = 0
    n_filled = 0
    existing_sources_by_date = existing_sources_by_date or {}
    for row in rows:
        d_iso = row["date"]
        try:
            d = date.fromisoformat(d_iso)
        except (TypeError, ValueError):
            continue
        if d < since:
            continue
        # Which micros are still None on this row?
        missing = [f for f in _MICRO_FIELDS if row.get(f) is None]
        if not missing:
            continue
        meals = meals_by_date.get(d) or []
        food_items = (food_items_by_date or {}).get(d) or []
        # The LLM needs SOME anchor: either meal totals (with names) or the
        # rich named-ingredient list. If both are empty, we cannot estimate.
        if not meals and not food_items:
            continue
        # Idempotency: skip if every missing field already has a recorded
        # source upstream (meaning we ran the estimator before and it failed
        # to anchor any value). Without `force`, don't waste an API call.
        if not force:
            prior = existing_sources_by_date.get(d_iso, {})
            if missing and all(prior.get(f) is not None for f in missing):
                continue

        existing_micros: dict[str, float | None] = {
            f: row.get(f) for f in _MICRO_FIELDS
        }
        daily_macros = {
            "kcal": row.get("kcal"),
            "protein_g": row.get("protein_g"),
            "carb_g": row.get("carb_g"),
            "fat_g": row.get("fat_g"),
        }
        try:
            estimates = enrich_estimation.estimate_day_micros(
                d_iso, meals, existing_micros, daily_macros, food_items=food_items
            )
        except Exception as e:
            print(
                f"[features] enrich_estimation failed for {d_iso}: {e}",
                file=sys.stderr,
            )
            continue
        n_dates += 1
        if not estimates:
            continue
        for field, payload in estimates.items():
            if field not in _MICRO_FIELDS:
                continue
            row[field] = payload["value"]
            row[f"{field}_source"] = payload["source"]
            n_filled += 1
            # Re-derive pct_e_sat when SFA was estimated.
            if field == "fat_sat_g":
                kcal = row.get("kcal")
                if kcal and kcal > 0 and payload["value"] is not None:
                    row["pct_e_sat"] = round(
                        (payload["value"] * 9.0) / kcal * 100.0, 2
                    )
    return n_dates, n_filled


def enrich_rows_with_per_item_estimates(
    rows: list[dict],
    food_items_by_date: dict[date, list[dict]],
    since: date,
) -> tuple[int, int, int]:
    """Per-item LLM enrichment for `is_ai_estimate=true` rows.

    The Yazio AI-photo / freestyle items (`source_kind='simple'`) land in
    `yazio_food_item_daily` with macros only -- saturated fat, sodium, sugar,
    fiber, alcohol are unknown. Yazio's day-level micronutrient totals are
    derived from `products` lookups only, so those items contribute 0 to the
    micro columns -> systematic under-count.

    For each AI item on each in-scope day, we ask Haiku (with the item NAME
    and its weight) for an item-level micros estimate, then ADD the sum to
    whatever Yazio already aggregated. Result becomes:
      - 'mixed'        -- when Yazio supplied something AND items added more
      - 'llm_estimate' -- when Yazio had nothing and items provided the only value

    Cached per (name, amount_g) across days via `enrich_estimation` -- the
    same dish logged 30 times costs 1 LLM call.

    Mutates rows in place. Returns (n_days_touched, n_items_estimated, n_llm_calls).
    """
    n_days = 0
    n_items = 0
    field_keys = ("fat_sat_g", "sodium_mg", "sugar_g", "fiber_g", "alcohol_g")
    start_calls = enrich_estimation.get_item_call_count()

    for row in rows:
        d_iso = row["date"]
        try:
            d = date.fromisoformat(d_iso)
        except (TypeError, ValueError):
            continue
        if d < since:
            continue
        items = food_items_by_date.get(d) or []
        ai_items = [it for it in items if it.get("is_ai_estimate")]
        if not ai_items:
            continue

        per_nutrient_topup: dict[str, float] = {k: 0.0 for k in field_keys}
        any_estimate = False
        for it in ai_items:
            amount = it.get("amount_g") or 0
            try:
                amount_f = float(amount)
            except (TypeError, ValueError):
                amount_f = 0.0
            if amount_f <= 0:
                continue
            # Derive item-level macros (g) from per-100g profile so the LLM
            # has a coherent picture (it sees grams, not per-100g rates).
            def _g(per100: Any) -> float | None:
                v = _to_num(per100)
                if v is None:
                    return None
                return v * amount_f / 100.0

            kcal = _g(it.get("kcal_per_100g"))
            protein_g = _g(it.get("protein_per_100g"))
            carb_g = _g(it.get("carb_per_100g"))
            fat_g_item = _g(it.get("fat_per_100g"))
            est = enrich_estimation.estimate_item_micros(
                name=it.get("name") or "",
                amount_g=amount_f,
                kcal=kcal,
                protein_g=protein_g,
                carb_g=carb_g,
                fat_g=fat_g_item,
            )
            if not est:
                continue
            any_estimate = True
            n_items += 1
            for k in field_keys:
                v = est.get(k)
                if v is None:
                    continue
                try:
                    per_nutrient_topup[k] += float(v)
                except (TypeError, ValueError):
                    continue

        if not any_estimate:
            continue

        touched = False
        for k in field_keys:
            topup = per_nutrient_topup.get(k, 0.0)
            if topup <= 0:
                continue
            current = row.get(k)
            current_source = row.get(f"{k}_source")
            # Never overwrite an LLM-reviewed sanitize verdict.
            if current_source == "llm_review":
                continue
            if current is None:
                row[k] = round(topup, 3)
                row[f"{k}_source"] = "llm_estimate"
            else:
                try:
                    new_val = float(current) + topup
                except (TypeError, ValueError):
                    continue
                row[k] = round(new_val, 3)
                row[f"{k}_source"] = "mixed"
            touched = True
            if k == "fat_sat_g":
                kcal_day = row.get("kcal")
                if kcal_day and kcal_day > 0 and row[k] is not None:
                    row["pct_e_sat"] = round(
                        (row[k] * 9.0) / kcal_day * 100.0, 2
                    )
        if touched:
            n_days += 1

    n_calls = enrich_estimation.get_item_call_count() - start_calls
    return n_days, n_items, n_calls


def enrich_rows_with_per_slot_estimates(
    rows: list[dict],
    meals_by_date: dict[date, list[dict]],
    food_items_by_date: dict[date, list[dict]],
    since: date,
    *,
    unresolved_fat_share_threshold: float = 0.02,
) -> tuple[int, int]:
    """Top up day-level micros when some meal slots have NO resolved food items.

    Problem: when the user logs via Yazio's photo-AI, an item lands in
    `yazio_meal` (macros) but NOT in `yazio_food_item_daily`. Consequence:
    `yazio_micronutrient_daily` only aggregates the *resolved* items, so
    saturated fat / sodium / sugar / fiber / alcohol are SYSTEMATICALLY
    under-counted on those days -- even though `fat_sat_g_source='yazio'`
    suggests the value is trustworthy.

    Fix: detect per-slot coverage. For every slot WITHOUT food items whose
    macro contribution is significant (>= `unresolved_fat_share_threshold`
    of day fat), ask Haiku to estimate that slot's micros from its macros
    alone, then ADD the estimates to whatever Yazio already aggregated for
    the day.

    Mutates rows in place. Returns (n_days_enriched, n_slot_calls).

    Sources after this pass:
      - 'yazio'        -- unchanged, no unresolved slots / negligible share
      - 'llm_estimate' -- unchanged (full-day LLM ran earlier; no Yazio value)
      - 'mixed'        -- Yazio value + per-slot LLM top-up
    """
    n_days = 0
    n_calls = 0
    # Map kept by date for fast lookup of which slots are resolved.
    resolved_slots_by_date: dict[date, set[str]] = defaultdict(set)
    for d, items in food_items_by_date.items():
        for it in items or []:
            slot = it.get("meal_slot") or it.get("meal")
            if slot:
                resolved_slots_by_date[d].add(slot)

    field_keys = ("fat_sat_g", "sodium_mg", "sugar_g", "fiber_g", "alcohol_g")

    for row in rows:
        d_iso = row["date"]
        try:
            d = date.fromisoformat(d_iso)
        except (TypeError, ValueError):
            continue
        if d < since:
            continue
        meals = meals_by_date.get(d) or []
        if not meals:
            continue
        resolved_slots = resolved_slots_by_date.get(d, set())

        # Total day-level fat as reported across yazio_meal rows. Used to
        # judge whether unresolved slots are significant enough to warrant
        # an LLM call.
        day_fat = 0.0
        for m in meals:
            v = m.get("fat_g")
            if v is None:
                continue
            try:
                day_fat += float(v)
            except (TypeError, ValueError):
                pass

        # Collect unresolved slots with their macros.
        unresolved: list[dict] = []
        for m in meals:
            slot = m.get("meal") or m.get("name")
            if not slot or slot in resolved_slots:
                continue
            slot_fat = m.get("fat_g")
            try:
                slot_fat_f = float(slot_fat) if slot_fat is not None else 0.0
            except (TypeError, ValueError):
                slot_fat_f = 0.0
            unresolved.append({"slot": slot, "macros": m, "fat": slot_fat_f})

        if not unresolved:
            continue

        # Significance gate: combined fat share of unresolved slots vs day.
        unresolved_fat = sum(u["fat"] for u in unresolved)
        if day_fat <= 0:
            continue
        if unresolved_fat / day_fat < unresolved_fat_share_threshold:
            continue

        # Per-slot LLM estimates -> sum per nutrient.
        per_nutrient_topup: dict[str, float] = {k: 0.0 for k in field_keys}
        any_call = False
        for u in unresolved:
            m = u["macros"]
            est = enrich_estimation.estimate_slot_micros(
                d_iso,
                u["slot"],
                m.get("kcal"),
                m.get("protein_g"),
                m.get("carb_g"),
                m.get("fat_g"),
            )
            if not est:
                continue
            any_call = True
            n_calls += 1
            for k in field_keys:
                v = est.get(k)
                if v is None:
                    continue
                try:
                    per_nutrient_topup[k] += float(v)
                except (TypeError, ValueError):
                    continue

        if not any_call:
            continue

        touched = False
        for k in field_keys:
            topup = per_nutrient_topup.get(k, 0.0)
            if topup <= 0:
                continue
            current = row.get(k)
            current_source = row.get(f"{k}_source")
            # We never overwrite an LLM-reviewed sanitize verdict.
            if current_source == "llm_review":
                continue
            if current is None:
                # No Yazio value at all -- the full-day estimator should have
                # run, but if it didn't anchor this nutrient, use the per-slot
                # top-up as the sole value.
                row[k] = round(topup, 3)
                row[f"{k}_source"] = "llm_estimate"
            else:
                try:
                    new_val = float(current) + topup
                except (TypeError, ValueError):
                    continue
                row[k] = round(new_val, 3)
                # 'mixed' = Yazio resolved items + LLM-estimated unresolved slots
                row[f"{k}_source"] = "mixed"
            touched = True
            # Re-derive pct_e_sat if saturated fat changed.
            if k == "fat_sat_g":
                kcal = row.get("kcal")
                if kcal and kcal > 0 and row[k] is not None:
                    row["pct_e_sat"] = round(
                        (row[k] * 9.0) / kcal * 100.0, 2
                    )
        if touched:
            n_days += 1
    return n_days, n_calls


def _load_existing_sources() -> dict[str, dict[str, str | None]]:
    """Map date_iso -> {field_source: value} for the source columns already on
    daily_features. Used by backrun --skip-existing and as cheap idempotency
    for the cron path."""
    try:
        rows = sb_get(
            "daily_features",
            {
                "select": (
                    "date,fat_sat_g_source,sodium_mg_source,sugar_g_source,"
                    "fiber_g_source,alcohol_g_source"
                ),
            },
        )
    except Exception as e:
        print(
            f"[features] could not load existing source columns: {e}",
            file=sys.stderr,
        )
        return {}
    out: dict[str, dict[str, str | None]] = {}
    for r in rows:
        d = str(r.get("date") or "")[:10]
        if not d:
            continue
        out[d] = {
            f"{f}_source": r.get(f"{f}_source") for f in _MICRO_FIELDS
        }
    return out


# ---------- z-scores -----------------------------------------------------

ZSCORE_FIELDS = [
    "kcal",
    "weight_kg",
    "sbp",
    "sleep_total_min",
    "alcohol_g",
    "fat_sat_g",
    "sodium_mg",
    "hr_rest_min",
]

Z_WINDOWS = [
    (28, 14, "z28"),  # window_days, min_n, prefix
    (84, 42, "z84"),
]


def compute_zscores(rows: list[dict]) -> None:
    """Mutate rows in place, adding z28_* / z84_* fields.
    `rows` must be sorted by date ascending. For each metric and each window,
    z = (val - mean) / std over the *prior* N days (excluding the current day),
    NULL if n < min_n or std < EPS or val is NULL.
    """
    rows.sort(key=lambda r: r["date"])
    n = len(rows)

    # pre-extract series of (date, value) per field for fast windowing
    series: dict[str, list[tuple[date, float | None]]] = {
        f: [(date.fromisoformat(r["date"]), _to_num(r.get(f))) for r in rows]
        for f in ZSCORE_FIELDS
    }

    for i, row in enumerate(rows):
        d_i = date.fromisoformat(row["date"])
        for window_days, min_n, prefix in Z_WINDOWS:
            cutoff = d_i - timedelta(days=window_days)
            for f in ZSCORE_FIELDS:
                key = f"{prefix}_{f}"
                val = series[f][i][1]
                if val is None:
                    row[key] = None
                    continue
                # walk back from i-1 collecting values within window
                vals: list[float] = []
                j = i - 1
                while j >= 0:
                    d_j, v_j = series[f][j]
                    if d_j < cutoff:
                        break
                    if v_j is not None:
                        vals.append(v_j)
                    j -= 1
                if len(vals) < min_n:
                    row[key] = None
                    continue
                m = sum(vals) / len(vals)
                # Bessel-corrected sample variance: vals is a sample of the
                # user's distribution, not the full population. With min_n=14
                # the bias was ~3.6 %, enough to inflate small z-scores.
                var = sum((x - m) ** 2 for x in vals) / (len(vals) - 1)
                sd = math.sqrt(var)
                if sd < EPS:
                    row[key] = None
                    continue
                row[key] = round((val - m) / sd, 3)


# ---------- main ---------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Populate daily_features.")
    p.add_argument(
        "--since",
        type=str,
        default=None,
        help="ISO date. Default = today UTC - 120 days. Use 2017-01-01 for full backfill.",
    )
    return p.parse_args()


def _max_ts(table: str, col: str = "ingested_at") -> str | None:
    try:
        rows = sb_get(table, {"select": col, "order": f"{col}.desc.nullslast", "limit": "1"})
    except Exception:
        return None
    if not rows:
        return None
    return rows[0].get(col)


def _features_fail_fast() -> bool:
    """Return True if we should skip the run (no new ingest since last features build).

    Compares max(ingested_at) across source tables vs max(computed_at) on
    daily_features. Bypassed when FORCE_REBUILD=1 or --since/--force is set.
    """
    if os.environ.get("FORCE_REBUILD"):
        return False
    last_features = _max_ts("daily_features", "computed_at")
    if last_features is None:
        return False  # never built, definitely run
    sources = (
        ("yazio_day", "ingested_at"),
        ("yazio_micronutrient_daily", "ingested_at"),
        ("yazio_food_item_daily", "ingested_at"),
        ("withings_measurement", "ingested_at"),
        ("withings_activity_daily", "ingested_at"),
        ("huawei_daily", None),  # no ingested_at column; skip
        ("wegovy_injection", "logged_at"),
    )
    latest_source: str | None = None
    for table, col in sources:
        if col is None:
            continue
        ts = _max_ts(table, col)
        if ts and (latest_source is None or ts > latest_source):
            latest_source = ts
    if latest_source is None:
        return False
    return latest_source <= last_features


def main() -> None:
    args = parse_args()
    today = datetime.now(timezone.utc).date()
    if args.since:
        since = date.fromisoformat(args.since)
    else:
        since = today - timedelta(days=120)
        if _features_fail_fast():
            print("[features] no new source data since last build, skip", file=sys.stderr)
            return

    print(f"[features] since={since} today={today}", file=sys.stderr)

    yz_by_d = load_yazio_day()
    meals_by_d = load_yazio_meals()
    food_items_by_d = load_yazio_food_items()
    micros_by_d = load_yazio_micros()
    wm_by_d = load_withings_measurements()
    wa_by_d = load_withings_activity()
    hu_by_d = load_huawei()
    hc_sleep_by_d = load_healthconnect_sleep()
    # Fold HC sleep into the huawei dict as a fallback: huawei_daily wins when
    # present (it has deep/rem stages), HC fills the gap when huawei_daily
    # has nothing for a date (Android APK is the live source since the
    # Huawei export pipeline was abandoned).
    n_hc_only = 0
    for d, hc in hc_sleep_by_d.items():
        existing = hu_by_d.get(d)
        if existing is None:
            hu_by_d[d] = dict(hc)
            n_hc_only += 1
        elif not existing.get("sleep_total_min"):
            existing["sleep_total_min"] = hc.get("sleep_total_min")
    print(
        f"[features] HC sleep merged: {len(hc_sleep_by_d)} date(s) from "
        f"hc_raw_record, {n_hc_only} of which had no huawei_daily row",
        file=sys.stderr,
    )
    wegovy_ladder = load_wegovy()

    # Universe of dates with any data, clipped to [since, today].
    # Z-scores need history, so we also fetch dates BEFORE `since` (going back
    # up to 84 days) purely to feed the rolling stats — but we only upsert
    # rows whose date >= since.
    all_dates: set[date] = set()
    for src in (yz_by_d, micros_by_d, wm_by_d, wa_by_d, hu_by_d, hc_sleep_by_d):
        all_dates.update(src.keys())
    if not all_dates:
        print("[features] no source data found", file=sys.stderr)
        return

    min_d = min(all_dates)
    max_d = min(max(all_dates), today)
    # Need 84d of context before `since` for the z84 window.
    context_start = max(min_d, since - timedelta(days=84))

    full_dates = sorted({d for d in all_dates if context_start <= d <= max_d})
    print(
        f"[features] sources loaded: yazio_day={len(yz_by_d)} micros={len(micros_by_d)} "
        f"withings_meas_days={len(wm_by_d)} withings_act={len(wa_by_d)} huawei={len(hu_by_d)} "
        f"wegovy={len(wegovy_ladder)} | dates_in_scope={len(full_dates)}",
        file=sys.stderr,
    )

    raw_rows = [
        build_row_raw(
            d,
            yz_by_d.get(d),
            micros_by_d.get(d, {}),
            wm_by_d.get(d, {}),
            wa_by_d.get(d),
            hu_by_d.get(d),
            wegovy_ladder,
        )
        for d in full_dates
    ]

    # Pull out corrections collected per row, then strip the helper field
    # so it doesn't leak into the upsert payload.
    all_corrections: list[sanitize.Correction] = []
    for r in raw_rows:
        cs = r.pop("__corrections", None) or []
        if date.fromisoformat(r["date"]) >= since:
            all_corrections.extend(cs)

    # LLM second-opinion: try to refine each rule-fired correction. Falls
    # back to no-op if ANTHROPIC_API_KEY / anthropic SDK / Yazio creds are
    # missing -- the rule decision stays intact in that case.
    daily_kcal_by_date = {r["date"]: r.get("kcal") for r in raw_rows}
    all_corrections, llm_overrides = escalate_corrections_to_llm(
        all_corrections, daily_kcal_by_date
    )
    _apply_llm_overrides_to_rows(raw_rows, llm_overrides)
    if llm_overrides:
        print(
            f"[features] LLM refined {sum(len(v) for v in llm_overrides.values())} "
            f"value(s) across {len(llm_overrides)} day(s)",
            file=sys.stderr,
        )

    # Also apply any active llm_review_* row that did NOT come from this
    # build's rule pass (e.g. alcohol_false_zero is detected at scan time
    # against raw food_items, not as part of sanitize.apply). Without this
    # patch, the daily_features.alcohol_g for those days would remain at the
    # raw (0 g) Yazio value.
    existing_reviews = _load_active_llm_reviews()
    standalone_overrides: dict[str, dict[str, float | None]] = {}
    for (d_iso, nid), refined in existing_reviews.items():
        already = llm_overrides.get(d_iso, {})
        if nid in already:
            continue
        # Conservative: only apply standalone reviews that *raise* the value.
        # Veto/confirm verdicts on alcohol_false_zero return 0 or None on a
        # day where raw_value was 0 -- safe no-op for the daily_features
        # alcohol column. Skipping them avoids the risk of stomping on a
        # legitimate non-zero Yazio total that some other code path produced.
        if refined is None or float(refined) <= 0:
            continue
        standalone_overrides.setdefault(d_iso, {})[nid] = refined
    if standalone_overrides:
        _apply_llm_overrides_to_rows(raw_rows, standalone_overrides)
        print(
            f"[features] applied {sum(len(v) for v in standalone_overrides.values())} "
            f"standalone LLM review(s) across {len(standalone_overrides)} day(s)",
            file=sys.stderr,
        )

    persist_corrections(all_corrections)

    # LLM micronutrient estimation: fill the 5 micros for days where Yazio
    # only returned macros+kcal (typically photo-AI logged meals). Idempotent
    # via the *_source columns; cron re-runs skip days already processed.
    existing_sources = _load_existing_sources()
    n_dates_llm, n_filled_llm = enrich_rows_with_llm_estimates(
        raw_rows,
        meals_by_d,
        since,
        force=False,
        existing_sources_by_date=existing_sources,
        food_items_by_date=food_items_by_d,
    )
    if n_dates_llm:
        print(
            f"[features] LLM micro estimation: {n_dates_llm} day(s) processed, "
            f"{n_filled_llm} nutrient(s) filled",
            file=sys.stderr,
        )

    # Per-item LLM enrichment for is_ai_estimate=true rows (simple_products
    # logged via Yazio AI photo / freestyle). Much more accurate than the
    # per-slot heuristic below because we know the item NAME. Cached per
    # (name, amount_g) -- repeated dishes cost 1 LLM call across all days.
    n_item_days, n_items_est, n_item_calls = enrich_rows_with_per_item_estimates(
        raw_rows,
        food_items_by_d,
        since,
    )
    if n_item_days:
        print(
            f"[features] per-item LLM enrichment (AI): {n_item_days} day(s) "
            f"touched, {n_items_est} item(s) estimated, {n_item_calls} LLM call(s)",
            file=sys.stderr,
        )

    # Per-slot LLM top-up: when only some meal slots have resolved food items
    # (typical of Yazio's photo-AI flow), the day-level micros aggregated by
    # Yazio under-count the unresolved slots. Estimate those slots' micros
    # from their macros and ADD to the Yazio totals. Source becomes 'mixed'.
    n_slot_days, n_slot_calls = enrich_rows_with_per_slot_estimates(
        raw_rows,
        meals_by_d,
        food_items_by_d,
        since,
    )
    if n_slot_days:
        print(
            f"[features] per-slot LLM top-up: {n_slot_days} day(s) enriched, "
            f"{n_slot_calls} slot call(s)",
            file=sys.stderr,
        )

    compute_zscores(raw_rows)

    # Only upsert rows whose date >= since.
    to_upsert = [r for r in raw_rows if date.fromisoformat(r["date"]) >= since]
    n = sb_upsert(to_upsert, "daily_features", "date")
    print(f"[features] upserted {n} rows (>= {since})", file=sys.stderr)


# ---------- corrections persistence -------------------------------------

def _load_active_correction_keys() -> set[tuple[str, str, str]]:
    """Return the set of (date, nutrient_id, rule_key) for active corrections.

    Used to dedupe before insert so re-running the populator does not stack
    identical corrections every day. A correction is considered the same as
    long as it has not been reverted (reverted_at IS NULL).
    """
    rows = sb_get(
        "yazio_correction",
        {"select": "date,nutrient_id,rule_key", "reverted_at": "is.null"},
    )
    out: set[tuple[str, str, str]] = set()
    for r in rows:
        d = str(r.get("date") or "")[:10]
        nid = r.get("nutrient_id") or ""
        rk = r.get("rule_key") or ""
        if d and nid:
            out.add((d, nid, rk))
    return out


def persist_corrections(corrections: list[sanitize.Correction]) -> None:
    """Insert new corrections, skipping any whose (date, nutrient, rule)
    triple is already active in the table."""
    if not corrections:
        print("[features] no Yazio corrections to record", file=sys.stderr)
        return
    try:
        existing = _load_active_correction_keys()
    except Exception as e:  # pragma: no cover - network failure path
        print(f"[features] could not load existing corrections: {e}", file=sys.stderr)
        return

    # Also dedupe against the LLM-review shadow: if an active correction with
    # rule_key=f"llm_review_{c.rule_key}" already exists for (date, nutrient),
    # the LLM has already weighed in on this exact rule firing -- don't
    # re-emit the raw rule row (would otherwise stack on every cron tick).
    fresh: list[dict] = []
    for c in corrections:
        key = (c.date, c.nutrient_id, c.rule_key or "")
        if key in existing:
            continue
        shadow_key = (c.date, c.nutrient_id, f"llm_review_{c.rule_key or ''}")
        if shadow_key in existing:
            continue
        fresh.append(c.to_row())
        existing.add(key)

    if not fresh:
        print(
            f"[features] {len(corrections)} corrections produced; "
            "all already on file (no-op)",
            file=sys.stderr,
        )
        return

    # Plain insert (no upsert) — the unique key includes applied_at, so
    # POSTing fresh rows always succeeds. Dedup against the active set above
    # is what keeps the table from growing on every run.
    url = f"{env('SUPABASE_URL')}/rest/v1/yazio_correction"
    headers = {**sb_headers(), "Prefer": "return=minimal"}
    batch = 500
    for i in range(0, len(fresh), batch):
        chunk = fresh[i : i + batch]
        r = requests.post(url, headers=headers, data=json.dumps(chunk), timeout=60)
        if not r.ok:
            sys.exit(
                f"insert yazio_correction failed {r.status_code}: {r.text[:500]}"
            )
    print(
        f"[features] inserted {len(fresh)} new Yazio corrections "
        f"(skipped {len(corrections) - len(fresh)} already on file)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
