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

from ingest.yazio import llm_sanity, sanitize  # noqa: E402

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
    """
    if not corrections:
        return corrections, {}

    overrides: dict[str, dict[str, float | None]] = {}
    out: list[sanitize.Correction] = []
    for c in corrections:
        if c.source != "rule":
            out.append(c)
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

    return {
        "__corrections": _corrections_for_row,  # stripped before upsert
        "date": d.isoformat(),
        "kcal": kcal,
        "protein_g": protein_g,
        "carb_g": carb_g,
        "fat_g": fat_g,
        "fat_sat_g": fat_sat_g,
        "fiber_g": fiber_g,
        "sodium_mg": sodium_mg,
        "alcohol_g": alcohol_g,
        "sugar_g": sugar_g,
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
                var = sum((x - m) ** 2 for x in vals) / len(vals)
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


def main() -> None:
    args = parse_args()
    today = datetime.now(timezone.utc).date()
    if args.since:
        since = date.fromisoformat(args.since)
    else:
        since = today - timedelta(days=120)

    print(f"[features] since={since} today={today}", file=sys.stderr)

    yz_by_d = load_yazio_day()
    micros_by_d = load_yazio_micros()
    wm_by_d = load_withings_measurements()
    wa_by_d = load_withings_activity()
    hu_by_d = load_huawei()
    wegovy_ladder = load_wegovy()

    # Universe of dates with any data, clipped to [since, today].
    # Z-scores need history, so we also fetch dates BEFORE `since` (going back
    # up to 84 days) purely to feed the rolling stats — but we only upsert
    # rows whose date >= since.
    all_dates: set[date] = set()
    for src in (yz_by_d, micros_by_d, wm_by_d, wa_by_d, hu_by_d):
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

    persist_corrections(all_corrections)

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

    fresh: list[dict] = []
    for c in corrections:
        key = (c.date, c.nutrient_id, c.rule_key or "")
        if key in existing:
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
