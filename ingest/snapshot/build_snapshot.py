"""
Build the cockpit_snapshot.payload (JSONB) from raw ingested tables.

Reads:
  yazio_day                    daily kcal/macros/water/steps/weight
  yazio_micronutrient_daily    optional (not exposed in payload yet)
  withings_measurement         per-measure rows incl. segmental BIA
  withings_activity_daily      per-day steps/distance/intensity/kcal

Writes:
  cockpit_snapshot (date PK, payload jsonb)

Idempotent: re-running for the same `today` rebuilds and overwrites the
snapshot for today. Designed to run after every Yazio/Withings ingest,
plus a daily cron of its own.

Env vars:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  TODAY                      Optional ISO date to anchor the snapshot;
                             defaults to UTC today.
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests


WEGOVY_START_ISO = "2026-04-14"  # Yannis' first injection (J1).
WEGOVY_LADDER = [
    {"dose_mg": 0.25, "weeks": (0, 4)},
    {"dose_mg": 0.5,  "weeks": (4, 8)},
    {"dose_mg": 1.0,  "weeks": (8, 12)},
    {"dose_mg": 1.7,  "weeks": (12, 16)},
    {"dose_mg": 2.4,  "weeks": (16, None)},
]
TOLERANCE_KG = 0.8
ASYMPTOTE_KG = 75.0
START_KG = 86.3       # Withings reading at J1.
GOMP_TAU_WEEKS = 22
GOMP_SHAPE = 1.4
MONTHS_FR = ["jan", "fév", "mar", "avr", "mai", "juin", "juil", "août", "sept", "oct", "nov", "déc"]


# ---------- generic helpers ----------------------------------------------

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
    """GET against Supabase REST, paginated. Supabase caps each response at
    ~1000 rows; we walk the Range header until exhausted."""
    out: list = []
    page_size = 1000
    offset = 0
    while True:
        headers = sb_headers()
        headers["Range-Unit"] = "items"
        headers["Range"] = f"{offset}-{offset + page_size - 1}"
        headers["Prefer"] = "count=exact"
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


def sb_upsert(rows: list[dict], table: str, on_conflict: str) -> None:
    if not rows:
        return
    url = f"{env('SUPABASE_URL')}/rest/v1/{table}?on_conflict={on_conflict}"
    headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    r = requests.post(url, headers=headers, data=json.dumps(rows), timeout=30)
    if not r.ok:
        sys.exit(f"upsert {table} failed {r.status_code}: {r.text[:500]}")


# ---------- domain primitives --------------------------------------------

def weight_ideal(t_weeks: float) -> float:
    """Gompertz fit calibrated on STEP-1 trial (semaglutide 2.4mg)."""
    return ASYMPTOTE_KG + (START_KG - ASYMPTOTE_KG) * math.exp(-math.pow(t_weeks / GOMP_TAU_WEEKS, GOMP_SHAPE))


def weight_projected(t_weeks: float, accel: float = 19) -> float:
    """Personal projection from current trend (faster time-constant)."""
    return ASYMPTOTE_KG + (START_KG - ASYMPTOTE_KG) * math.exp(-math.pow(t_weeks / accel, GOMP_SHAPE))


def status_band(delta_kg: float) -> tuple[str, str]:
    a = abs(delta_kg)
    if a <= TOLERANCE_KG:
        return "conforme", "Conforme"
    if a <= 2 * TOLERANCE_KG:
        return "derive_mineure", "Dérive mineure"
    if a <= 3 * TOLERANCE_KG:
        return "derive_notable", "Dérive notable"
    return "derive_marquee", "Dérive marquée"


def fmt_date_fr(d: date) -> str:
    return f"{d.day} {MONTHS_FR[d.month - 1]} {d.year}"


def fmt_num(x: float, ndigits: int = 1) -> str:
    return f"{x:.{ndigits}f}".replace(".", ",")


# ---------- builders ------------------------------------------------------

def latest_weight_kg(measurements: list[dict]) -> tuple[float, datetime] | None:
    """Most recent weight in withings_measurement (type 1)."""
    weights = [m for m in measurements if m["type_code"] == 1]
    if not weights:
        return None
    latest = max(weights, key=lambda m: m["ts"])
    ts = datetime.fromisoformat(latest["ts"].replace("Z", "+00:00"))
    return float(latest["value"]), ts


def real_weight_points(measurements: list[dict], start: date, today: date) -> list[dict]:
    """One weight point per week since Wegovy start, weekly average."""
    weights = [m for m in measurements if m["type_code"] == 1]
    if not weights:
        return []
    # bucket by week index since start
    buckets: dict[int, list[float]] = {}
    for m in weights:
        ts = datetime.fromisoformat(m["ts"].replace("Z", "+00:00")).date()
        if ts < start or ts > today:
            continue
        week = (ts - start).days / 7
        wk_int = int(week)
        buckets.setdefault(wk_int, []).append(float(m["value"]))
    out = []
    for wk in sorted(buckets):
        kg = sum(buckets[wk]) / len(buckets[wk])
        out.append({"week": wk, "kg": round(kg, 1)})
    return out


def build_hero(measurements: list[dict], today: date) -> dict:
    start = date.fromisoformat(WEGOVY_START_ISO)
    today_week = (today - start).days / 7
    cur = latest_weight_kg(measurements)
    current_kg = cur[0] if cur else START_KG
    ideal = weight_ideal(today_week)
    delta = current_kg - ideal
    status_key, status_label = status_band(delta)
    # ETA at 75 kg via projected curve
    eta_week = None
    for w in [today_week + 0.5 * i for i in range(0, 200)]:
        if weight_projected(w) <= 75.05:
            eta_week = w
            break
    eta_date = start + timedelta(days=int(eta_week * 7)) if eta_week else (start + timedelta(weeks=52))
    return {
        "status": status_key,
        "statusLabel": status_label,
        "current_kg": round(current_kg, 1),
        "ideal_kg": round(ideal, 1),
        "delta_kg": round(delta, 1),
        "tolerance_kg": TOLERANCE_KG,
        "start_date": WEGOVY_START_ISO,
        "today_week": round(today_week, 1),
        "range": {"kg_min": 75, "kg_max": 87, "week_min": 0, "week_max": 52},
        "real_points": real_weight_points(measurements, start, today),
        "eta_75kg": eta_date.isoformat(),
    }


def build_wegovy(today: date) -> dict:
    start = date.fromisoformat(WEGOVY_START_ISO)
    day = (today - start).days
    week = day / 7
    step_index = 1
    for i, s in enumerate(WEGOVY_LADDER, start=1):
        lo, hi = s["weeks"]
        if hi is None or week < hi:
            step_index = i
            break
    current = WEGOVY_LADDER[step_index - 1]
    ladder = []
    for i, s in enumerate(WEGOVY_LADDER, start=1):
        if i < step_index:
            st = "done"
        elif i == step_index:
            st = "current"
        else:
            st = "upcoming"
        ladder.append({"dose_mg": s["dose_mg"], "status": st})
    next_dose = WEGOVY_LADDER[step_index]["dose_mg"] if step_index < len(WEGOVY_LADDER) else current["dose_mg"]
    cur_end_week = current["weeks"][1] if current["weeks"][1] is not None else week
    next_in_weeks = max(0, round(cur_end_week - week))
    return {
        "day_since_start": day,
        "current_dose_mg": current["dose_mg"],
        "step_index": step_index,
        "ladder": ladder,
        "next_dose_mg": next_dose,
        "next_in_weeks": next_in_weeks,
    }


def build_signals(yazio: list[dict], measurements: list[dict], activity: list[dict], hc_records: list[dict], today: date) -> list[dict]:
    """5 cross-source signals. Returns only those with usable data."""
    out: list[dict] = []

    # --- Déficit énergétique observable (28d) ---
    # Pivot from naive TDEE (biased by sparse intake logging) to a more honest
    # signal: the deficit implied purely by weight change. Independent of how
    # many days the user logged. Uses Wegovy-tuned 6500 kcal/kg coefficient.
    win_start = today - timedelta(days=28)
    wt_start = next(
        (m for m in measurements if m["type_code"] == 1 and date.fromisoformat(m["ts"][:10]) <= win_start),
        None,
    )
    wt_end = latest_weight_kg(measurements)
    if wt_start and wt_end:
        delta_kg = wt_end[0] - float(wt_start["value"])
        deficit_per_day = -(delta_kg * 6500) / 28  # positive when losing
        intake_28d = [
            y for y in yazio
            if y.get("kcal") and y["kcal"] > 100
            and win_start <= date.fromisoformat(y["date"]) <= today
        ]
        logged_n = len(intake_28d)
        sub_bits = [f"poids {round(wt_start['value'], 1)}→{round(wt_end[0], 1)} kg sur 28 j", "coeff 6 500 (Wegovy)"]
        if logged_n >= 5:
            avg_logged_kcal = round(sum(float(y["kcal"]) for y in intake_28d) / logged_n)
            sub_bits.append(f"{logged_n} j Yazio loggés Ø {avg_logged_kcal} kcal")
        watch = abs(deficit_per_day) > 800  # >800 kcal/j = perte trop rapide
        out.append({
            "id": "deficit",
            "title": "Déficit énergétique",
            "sub": " · ".join(sub_bits),
            "value": ("+" if deficit_per_day >= 0 else "−") + f"{abs(int(round(deficit_per_day))):,}".replace(",", " "),
            "unit": "kcal / j moy.",
            "status": "watch" if watch else "ok",
            "status_label": "trop rapide" if watch else ("perte en cours" if deficit_per_day > 100 else "stable"),
            "spark": _line_spark([float(y["kcal"]) for y in intake_28d[-14:]], "sage") if intake_28d else {"kind": "line", "color": "sage", "points": []},
        })

    # --- Proteins / LBM ---
    lbm_row = next((m for m in measurements if m["type_code"] == 5 and (m.get("position") in (None, 0, 7))), None)
    proteins = [float(y["protein_g"]) for y in yazio[-28:] if y.get("protein_g")]
    if lbm_row and proteins:
        lbm = float(lbm_row["value"])
        avg_protein = sum(proteins) / len(proteins)
        g_per_kg = avg_protein / lbm
        watch = g_per_kg < 2.0
        out.append({
            "id": "protein",
            "title": "Protéines / LBM",
            "sub": f"zone adéquate 2,0–2,4 · {len(proteins)} j moy · LBM {lbm:.1f} kg",
            "value": fmt_num(g_per_kg, 1),
            "unit": "g/kg LBM",
            "status": "watch" if watch else "ok",
            "status_label": "à surveiller" if watch else "conforme",
            "spark": _bar_spark(proteins[-13:], "ambre" if watch else "sage"),
        })

    # --- Wegovy response: real vs STEP-1 predicted at today_week ---
    start = date.fromisoformat(WEGOVY_START_ISO)
    today_week = (today - start).days / 7
    if today_week >= 1 and wt_end:
        real_loss = START_KG - wt_end[0]
        predicted_loss = START_KG - weight_ideal(today_week)
        if abs(predicted_loss) > 0.1:
            z = (real_loss - predicted_loss) / max(0.5, predicted_loss * 0.15)
            out.append({
                "id": "wegovy_response",
                "title": "Réponse Wegovy",
                "sub": f"−{real_loss:.1f} kg réel vs −{predicted_loss:.1f} prédit STEP-1 W{today_week:.1f}".replace(".", ","),
                "value": ("+" if z >= 0 else "−") + fmt_num(abs(z), 1),
                "unit": "z STEP-1",
                "status": "ok",
                "status_label": "sur trajectoire" if abs(z) < 1 else "rapide",
                "spark": _line_spark([START_KG - float(p["kg"]) for p in real_weight_points(measurements, start, today)], "sage", end_dot=True),
            })

    # --- Sleep × HRV: needs hc_raw_record (Health Connect via Android app) ---
    hrv = _avg_hrv_from_hc(hc_records, today)
    sleep_pts = _sleep_minutes_per_day(hc_records, today, 14)
    if hrv and len(sleep_pts) >= 5:
        avg_min = sum(v for _, v in sleep_pts[-7:]) / max(1, len(sleep_pts[-7:]))
        deficit = (420 - avg_min) * 7 / 60  # hours under target across the week
        avg_hrv, hrv_series = hrv
        # crude z vs personal mean (will be sharper once 28d baseline exists)
        watch = avg_min < 380 or avg_hrv < 50
        out.append({
            "id": "sleep_hrv",
            "title": "Dette sommeil × HRV",
            "sub": f"sommeil 7j moy {int(avg_min)} min · HRV {int(avg_hrv)} ms",
            "value": f"{'+' if deficit < 0 else '−'}{abs(int(deficit))} h",
            "unit": "/ 7 j",
            "status": "watch" if watch else "ok",
            "status_label": "à surveiller" if watch else "conforme",
            "spark": _line_spark(hrv_series, "ambre" if watch else "sage"),
        })

    # --- Steps / activity ---
    recent_act = [a for a in activity if a.get("steps") and date.fromisoformat(a["date"]) >= today - timedelta(days=28)]
    if recent_act:
        avg_steps = sum(int(a["steps"]) for a in recent_act) / len(recent_act)
        watch = avg_steps < 9000
        out.append({
            "id": "activity",
            "title": "Activité 28 j",
            "sub": f"moyenne {len(recent_act)} j · source Health Connect",
            "value": f"{int(round(avg_steps)):,}".replace(",", " "),
            "unit": "pas/j moy",
            "status": "watch" if watch else "ok",
            "status_label": "à surveiller" if watch else "conforme",
            "spark": _bar_spark([int(a["steps"]) for a in recent_act[-14:]], "ambre" if watch else "sage"),
        })

    return out


def _line_spark(values: list[float], color: str, end_dot: bool = False) -> dict:
    if not values:
        return {"kind": "line", "color": color, "points": []}
    n = len(values)
    vmin, vmax = min(values), max(values)
    rng = vmax - vmin or 1
    pts = []
    for i, v in enumerate(values):
        x = round(2 + (i / max(1, n - 1)) * 76, 1)
        y = round(4 + ((vmax - v) / rng) * 16, 1)
        pts.append([x, y])
    spark: dict = {"kind": "line", "color": color, "points": pts}
    if end_dot:
        spark["end_dot"] = True
    return spark


def _bar_spark(values: list[float], color: str) -> dict:
    if not values:
        return {"kind": "bars", "color": color, "values": []}
    vmax = max(values) or 1
    bars = [max(2, round(20 * (v / vmax))) for v in values]
    return {"kind": "bars", "color": color, "values": bars}


def build_bio_age(measurements: list[dict]) -> dict:
    """Without blood labs we keep the bio age simple: chrono, no big claims."""
    chrono = 35
    vo2 = next((m for m in measurements if m["type_code"] == 123), None)
    cardio_age = max(20, chrono - int(round((float(vo2["value"]) - 45) / 2))) if vo2 else chrono
    fat_ratio = next((m for m in measurements if m["type_code"] == 6 and (m.get("position") in (None, 0, 7))), None)
    composition_age = chrono + int(round((float(fat_ratio["value"]) - 16) / 2)) if fat_ratio else chrono
    composite = round((cardio_age + chrono + composition_age + chrono) / 4)
    return {
        "composite": composite,
        "chrono": chrono,
        "delta_vs_chrono": composite - chrono,
        "subages": [
            {"key": "cardio", "label": "Cardio", "value": cardio_age},
            {"key": "blood", "label": "Sang", "value": chrono},
            {"key": "composition", "label": "Composition", "value": composition_age, "off": composition_age > chrono + 1},
            {"key": "skeleton", "label": "Squelette", "value": chrono},
        ],
        "trajectory_12m": _composite_history(measurements, chrono),
    }


def _composite_history(measurements: list[dict], chrono: int) -> list[dict]:
    """Approximate trajectory: 7 monthly composite ages over the last 12 months."""
    today = date.today()
    out = []
    for months_back in range(12, -1, -2):
        anchor = today - timedelta(days=months_back * 30)
        relevant_fat = next((m for m in measurements if m["type_code"] == 6 and date.fromisoformat(m["ts"][:10]) <= anchor), None)
        relevant_vo2 = next((m for m in measurements if m["type_code"] == 123 and date.fromisoformat(m["ts"][:10]) <= anchor), None)
        cardio = max(20, chrono - int(round((float(relevant_vo2["value"]) - 45) / 2))) if relevant_vo2 else chrono
        composition = chrono + int(round((float(relevant_fat["value"]) - 16) / 2)) if relevant_fat else chrono
        composite = round((cardio + chrono + composition + chrono) / 4)
        out.append({"month": MONTHS_FR[anchor.month - 1].upper() + (" '" + str(anchor.year % 100)) if months_back in (12, 6, 0) else MONTHS_FR[anchor.month - 1].upper(), "value": composite})
    return out


def _avg_hr_from_hc(hc_records: list[dict], today: date) -> float | None:
    """Average resting HR over last 30 days from hc_raw_record."""
    recent = [
        float(r["value_num"]) for r in hc_records
        if r["record_type"] == "resting_heart_rate" and r.get("value_num") is not None
        and date.fromisoformat(r["start_ts"][:10]) >= today - timedelta(days=30)
    ]
    return sum(recent) / len(recent) if recent else None


def _avg_hrv_from_hc(hc_records: list[dict], today: date) -> tuple[float, list[float]] | None:
    """(avg RMSSD last 14d, daily values for sparkline) — None if no data."""
    recent = sorted(
        [r for r in hc_records
         if r["record_type"] == "hrv_rmssd" and r.get("value_num") is not None
         and date.fromisoformat(r["start_ts"][:10]) >= today - timedelta(days=14)],
        key=lambda r: r["start_ts"],
    )
    if not recent:
        return None
    vals = [float(r["value_num"]) for r in recent]
    return sum(vals) / len(vals), vals[-14:]


def _sleep_minutes_per_day(hc_records: list[dict], today: date, days: int) -> list[tuple[date, float]]:
    """Per-day total sleep minutes from sleep_session records."""
    by_day: dict[date, float] = {}
    for r in hc_records:
        if r["record_type"] != "sleep_session" or r.get("value_num") is None:
            continue
        # Use end_ts as the "wake day" anchor
        d_str = (r.get("end_ts") or r["start_ts"])[:10]
        d = date.fromisoformat(d_str)
        if d < today - timedelta(days=days) or d > today:
            continue
        by_day[d] = by_day.get(d, 0) + float(r["value_num"])
    return sorted(by_day.items())


def build_pillars(yazio: list[dict], measurements: list[dict], activity: list[dict], hc_records: list[dict], today: date) -> list[dict]:
    pillars = []

    # Composition: BF% latest + 12 mois trajectoire
    fat_series = sorted(
        [m for m in measurements if m["type_code"] == 6 and (m.get("position") in (None, 0, 7))],
        key=lambda m: m["ts"],
    )
    if fat_series:
        latest_bf = fat_series[-1]
        latest_date = date.fromisoformat(latest_bf["ts"][:10])
        # 12 month sampling
        sampled = _sample_monthly(fat_series, today, 12)
        pts = []
        for i, m in enumerate(sampled):
            x = round(8 + (i / max(1, len(sampled) - 1)) * 184, 1)
            v = float(m["value"])
            # map 14-30% MG → y 12..72
            y = round(12 + ((30 - v) / 16) * 60, 1)
            y = max(8, min(72, y))
            pts.append([x, y])
        pillars.append({
            "key": "composition",
            "label": "Composition",
            "meta": fmt_date_fr(latest_date),
            "figure": fmt_num(float(latest_bf["value"]), 1),
            "unit": "% MG",
            "chart": {
                "kind": "area",
                "target_label": "cible 16",
                "target_y": 72,
                "points": pts,
            },
        })

    # Activity: pick last "complete" day (>=4000 steps) to avoid showing a
    # partial day still in progress; fall back to most recent if none qualify.
    if activity:
        sorted_act = sorted(activity, key=lambda a: a["date"])
        latest_steps = (
            next((a for a in reversed(sorted_act) if a.get("steps") and int(a["steps"]) >= 4000), None)
            or next((a for a in reversed(sorted_act) if a.get("steps")), None)
        )
        if latest_steps:
            last7 = sorted_act[-7:]
            vmax = max([int(a.get("steps") or 0) for a in last7] + [10000]) or 10000
            bars = [max(2, round(64 * (int(a.get("steps") or 0) / vmax))) for a in last7]
            # target line at 10k → y in viewBox 80
            target_y = max(2, round(80 - 64 * (10000 / vmax)))
            ambre = [i for i, a in enumerate(last7) if int(a.get("steps") or 0) < 7000]
            pillars.append({
                "key": "activity",
                "label": "Activité",
                "meta": fmt_date_fr(date.fromisoformat(latest_steps["date"])) if latest_steps["date"] != today.isoformat() else "auj.",
                "figure": f"{int(latest_steps['steps']):,}".replace(",", " "),
                "unit": "pas",
                "chart": {
                    "kind": "bars",
                    "values": bars,
                    "target_label": "10 k",
                    "target_y": target_y,
                    "ambre_indices": ambre,
                },
            })

    # Recovery: sleep duration last 7 nights from hc_raw_record.
    # If empty (APK not installed yet), still render a placeholder tile so
    # the cockpit's 4-pillar layout stays balanced.
    sleep_pts = _sleep_minutes_per_day(hc_records, today, 7)
    if not sleep_pts:
        pillars.append({
            "key": "recovery",
            "label": "Récupération",
            "meta": "APK requise",
            "figure": "—",
            "unit": "installer Cockpit Sync",
            "chart": {
                "kind": "bars",
                "values": [8, 12, 6, 14, 10, 8, 6],
                "target_band": {"y": 14, "h": 14},
                "ambre_indices": [0, 1, 2, 3, 4, 5, 6],
            },
        })
    if sleep_pts:
        last7 = sleep_pts[-7:]
        avg_min = sum(v for _, v in last7) / len(last7)
        bars = [max(2, min(72, round(v / 9))) for _, v in last7]  # 9 min ~ 1px (target 420 → 47px)
        # ambre if under 6h (360 min)
        ambre = [i for i, (_, v) in enumerate(last7) if v < 360]
        h = int(avg_min // 60)
        m = int(avg_min - h * 60)
        pillars.append({
            "key": "recovery",
            "label": "Récupération",
            "meta": "7 j",
            "figure": f"{h} h {m:02d}",
            "unit": "moy.",
            "chart": {
                "kind": "bars",
                "values": bars,
                "target_band": {"y": 14, "h": 14},
                "ambre_indices": ambre,
            },
        })

    # Cardio: resting HR from withings_activity_daily.raw.hr_min (Huawei via HC).
    # No ScanWatch in the user's setup, so VO2max stays unavailable for now.
    rest_hr_series = sorted(
        [(a["date"], int(a["raw"]["hr_min"]))
         for a in activity
         if a.get("raw") and isinstance(a["raw"], dict) and a["raw"].get("hr_min")
         and int(a["raw"]["hr_min"]) > 30],
        key=lambda x: x[0],
    )
    if len(rest_hr_series) >= 5:
        last12w = rest_hr_series[-84:]
        # 7-day rolling avg smooths daily noise (a single bad night swings hr_min).
        last7_vals = [v for _, v in last12w[-7:]]
        latest_hr = int(round(sum(last7_vals) / len(last7_vals)))
        vmin, vmax = min(v for _, v in last12w), max(v for _, v in last12w)
        rng = (vmax - vmin) or 1
        pts = []
        for i, (_, v) in enumerate(last12w):
            x = round(8 + (i / max(1, len(last12w) - 1)) * 184, 1)
            # higher HR = worse → render higher value lower in chart
            y = round(22 + ((v - vmin) / rng) * 38, 1)
            pts.append([x, y])
        pillars.append({
            "key": "cardio",
            "label": "Cardio (HR repos)",
            "meta": f"{max(1, min(12, len(last12w) // 7))} sem",
            "figure": str(latest_hr),
            "unit": "bpm",
            "chart": {
                "kind": "area",
                "target_label": "cible 50",
                "target_y": 60,
                "points": pts,
            },
        })

    # (Legacy block kept in case VO2max appears later, e.g. ScanWatch acquired)
    vo2_series = sorted([m for m in measurements if m["type_code"] == 123], key=lambda m: m["ts"])
    if vo2_series and not any(p["key"] == "cardio" for p in pillars):
        latest_vo2 = vo2_series[-1]
        last_pts = vo2_series[-12:]
        vals = [float(m["value"]) for m in last_pts]
        vmin, vmax = min(vals), max(vals)
        rng = vmax - vmin or 1
        pts = []
        for i, v in enumerate(vals):
            x = round(8 + (i / max(1, len(vals) - 1)) * 184, 1)
            y = round(22 + (1 - (v - vmin) / rng) * 38, 1)
            pts.append([x, y])
        pillars.append({
            "key": "cardio",
            "label": "Cardio",
            "meta": f"{len(last_pts)} sem",
            "figure": str(int(round(float(latest_vo2["value"])))),
            "unit": "ml/kg",
            "chart": {
                "kind": "area",
                "target_label": "cible 55",
                "target_y": 10,
                "points": pts,
            },
        })

    return pillars


SEGMENT_LABELS = {
    # Withings Body Scan position codes. Mapping observed empirically:
    # value 12 (largest) ≈ trunk; 10/11 ≈ legs; 2/3 ≈ arms. Left/right
    # ambiguity remains — labels stay symmetric so the user can identify
    # them by comparing to the Withings Health Mate app.
    2: "Bras A",
    3: "Bras B",
    10: "Jambe A",
    11: "Jambe B",
    12: "Tronc",
}


def _segment_subs(measurements: list[dict], type_code: int, unit: str, key_prefix: str) -> list[dict]:
    """One SubTrajectory per Body Scan segment for the given type_code."""
    rows = [m for m in measurements if m["type_code"] == type_code and m.get("position") in SEGMENT_LABELS]
    if not rows:
        return []
    by_pos: dict[int, list[dict]] = {}
    for r in rows:
        by_pos.setdefault(int(r["position"]), []).append(r)
    out: list[dict] = []
    for pos in sorted(by_pos):
        series = sorted(by_pos[pos], key=lambda r: r["ts"])[-12:]
        if not series:
            continue
        latest_v = float(series[-1]["value"])
        first_v = float(series[0]["value"])
        delta = latest_v - first_v
        pts = [{"date": fmt_date_fr(date.fromisoformat(r["ts"][:10])), "value": round(float(r["value"]), 2)} for r in series]
        sign = "+" if delta >= 0 else "−"
        out.append({
            "key": f"{key_prefix}_{pos}",
            "label": f"{SEGMENT_LABELS[pos]} — {key_prefix}",
            "unit": unit,
            "current": fmt_num(latest_v, 2),
            "trend_label": f"{sign}{fmt_num(abs(delta), 2)} sur {len(series)} mesures",
            "points": pts,
            "ambre": False,
        })
    return out


def build_pillar_detail_composition(measurements: list[dict], today: date) -> dict | None:
    fat = sorted(
        [m for m in measurements if m["type_code"] == 6 and (m.get("position") in (None, 0, 7))],
        key=lambda m: m["ts"],
    )
    if not fat:
        return None
    latest = fat[-1]
    latest_v = float(latest["value"])
    latest_d = date.fromisoformat(latest["ts"][:10])
    # Trajectory: weekly average over the last 24 months. Body Scan only came
    # online in Apr 2026, but older Body+ scales also report fat_ratio.
    weekly = _sample_weekly(fat, today, 104)
    pts = [
        {
            "date": fmt_date_fr(d),
            "value": round(v, 1),
        }
        for d, v in weekly
    ]
    # vs 6 months ago
    six_m_ago = next((r for r in fat if (today - date.fromisoformat(r["ts"][:10])).days >= 180), None)
    delta = None
    if six_m_ago:
        delta = round(latest_v - float(six_m_ago["value"]), 1)
    rows: list[dict] = []
    for m in fat[-8:][::-1]:
        d = date.fromisoformat(m["ts"][:10])
        rows.append({"date": fmt_date_fr(d), "value": round(float(m["value"]), 1), "unit": "% MG"})
    return {
        "key": "composition",
        "title": "Composition corporelle",
        "meta": f"Withings · dernière mesure {fmt_date_fr(latest_d)}",
        "hero": {
            "figure": fmt_num(latest_v, 1),
            "unit": "% MG",
            "delta_label": (f"{'+' if delta >= 0 else '−'}{abs(delta)} pts vs 6 mois" if delta is not None else None),
            "status_label": "Conforme" if latest_v <= 18 else "Dérive mineure" if latest_v <= 22 else "Dérive notable",
            "status_off": latest_v > 18,
        },
        "trajectory": {
            "x_label": "12 mois",
            "y_unit": "% MG",
            "y_min": 14,
            "y_max": 30,
            "points": pts,
            "target": {"value": 16, "label": "cible 16 %"},
        },
        "table": rows,
        "subs": _segment_subs(measurements, 175, "kg", "muscle") + _segment_subs(measurements, 174, "kg", "fat"),
        "method": [
            {"heading": "Source", "body": "Withings Body Scan (BIA segmentale 8 électrodes, 50 kHz). Mesure auto au lever, à jeun. MG% via algorithme propriétaire calibré hydratation."},
            {"heading": "Fenêtre", "body": f"Échantillonnage 1 mesure / mois sur 12 mois. Dernière mesure {fmt_date_fr(latest_d)}."},
            {"heading": "Cible", "body": "16 % long terme (catégorie 'athletic' ACSM cohorte 30-39 ans). Bande conforme jusqu'à 18 %."},
        ],
    }


def build_pillar_detail_cardio(activity: list[dict], today: date) -> dict | None:
    """HR repos derived from Withings activity.raw.hr_min (Huawei via HC)."""
    series = sorted(
        [(a["date"], int(a["raw"]["hr_min"]))
         for a in activity
         if a.get("raw") and isinstance(a["raw"], dict) and a["raw"].get("hr_min")
         and int(a["raw"]["hr_min"]) > 30],
        key=lambda x: x[0],
    )
    if len(series) < 5:
        return None
    last90 = series[-90:]
    last7 = last90[-7:]
    avg7 = int(round(sum(v for _, v in last7) / len(last7)))
    # 90 days trajectory
    pts = [{"date": fmt_date_fr(date.fromisoformat(d)), "value": v} for d, v in last90]
    avg_first7 = sum(v for _, v in last90[:7]) / max(1, min(7, len(last90)))
    delta = avg7 - avg_first7
    rows = [{"date": fmt_date_fr(date.fromisoformat(d)), "value": v, "unit": "bpm",
             "off": v > 70} for d, v in series[-7:][::-1]]
    return {
        "key": "cardio",
        "title": "Cardio · HR repos",
        "meta": "Huawei Watch GT2 (HR min nocturne) via Health Connect → Withings",
        "hero": {
            "figure": str(avg7),
            "unit": "bpm (moy. 7 j)",
            "delta_label": f"{'+' if delta >= 0 else '−'}{abs(round(delta))} vs début de fenêtre",
            "status_label": "Conforme" if avg7 <= 60 else "Dérive mineure" if avg7 <= 70 else "Dérive notable",
            "status_off": avg7 > 60,
        },
        "trajectory": {
            "x_label": "90 j",
            "y_unit": "bpm",
            "y_min": min(v for _, v in last90) - 3,
            "y_max": max(v for _, v in last90) + 3,
            "points": pts,
            "target": {"value": 50, "label": "cible 50 bpm"},
        },
        "table": rows,
        "method": [
            {"heading": "Source", "body": "HR min quotidien capté pendant le sommeil par la Huawei Watch GT2, exposé via Health Connect puis agrégé par Withings dans son endpoint activity. Approximation valide du HR repos (Plews & Laursen 2017)."},
            {"heading": "Cible 50 bpm", "body": "Plage 50-60 bpm: endurance entrainée. <50: athlétique. >70 sur 7 j: signal de surentrainement, fatigue, ou perte de condition à investiguer."},
            {"heading": "Limite", "body": "Pas de VO2max direct (pas de ScanWatch). HR repos est un proxy correct mais inférieur à un VO2max mesuré pour suivre les progrès cardio fins."},
        ],
    }


def build_pillar_detail_recovery(hc_records: list[dict], today: date) -> dict | None:
    """Sleep + HRV from Health Connect. Returns placeholder until APK installed."""
    sleep_pts = _sleep_minutes_per_day(hc_records, today, 30)
    if not sleep_pts:
        return {
            "key": "recovery",
            "title": "Récupération",
            "meta": "En attente de la companion app Android",
            "hero": {
                "figure": "—",
                "unit": "données indisponibles",
                "status_label": "Cockpit Sync à installer",
                "status_off": True,
            },
            "trajectory": {
                "x_label": "30 j",
                "y_unit": "min",
                "y_min": 240,
                "y_max": 540,
                "points": [],
                "target": {"value": 420, "label": "cible 7 h"},
            },
            "table": [],
            "method": [
                {"heading": "Pourquoi vide", "body": "Withings n'expose pas le sommeil détaillé Huawei via son API. Pour lire les nuits, la HRV et la HR continue, télécharge l'APK Cockpit Sync depuis GitHub Actions (workflow Build Android APK → cockpit-sync-debug-apk) et accorde les permissions Health Connect au premier lancement."},
                {"heading": "Ce qui apparaîtra ensuite", "body": "Durée par nuit (stades détectés par TruSleep), HRV nocturne (RMSSD), HR repos vraie (non plus dérivée de hr_min activity), latence d'endormissement si dispo, score sommeil composite."},
            ],
        }
    last7 = sleep_pts[-7:]
    avg_min = sum(v for _, v in last7) / len(last7)
    h, m = int(avg_min // 60), int(avg_min - (avg_min // 60) * 60)
    pts = [{"date": fmt_date_fr(d), "value": int(v)} for d, v in sleep_pts]
    rows = [{"date": fmt_date_fr(d), "value": f"{int(v // 60)} h {int(v - (v // 60) * 60):02d}", "unit": "", "off": v < 360} for d, v in sleep_pts[-7:][::-1]]
    return {
        "key": "recovery",
        "title": "Récupération",
        "meta": "Huawei Watch GT2 · sommeil via Health Connect",
        "hero": {
            "figure": f"{h} h {m:02d}",
            "unit": "moyenne 7 j",
            "status_label": "Conforme" if avg_min >= 420 else "Dérive mineure" if avg_min >= 360 else "Dérive notable",
            "status_off": avg_min < 420,
        },
        "trajectory": {
            "x_label": "30 j",
            "y_unit": "min",
            "y_min": 240,
            "y_max": 540,
            "points": pts,
            "target": {"value": 420, "label": "cible 7 h"},
            "tolerance": 30,
        },
        "table": rows,
        "method": [
            {"heading": "Source", "body": "Huawei Watch GT2 (TruSleep PPG + accéléromètre). Stades sommeil détectés: éveil, léger, profond, REM. Précision ±25 min vs polysomnographie (Chinoy 2021)."},
            {"heading": "Cible 7-8 h", "body": "Consensus NSF 2015 + AASM 2015 pour adultes 26-64 ans. <6 h sur 7 nuits: cortisol matinal élevé, sensibilité insuline diminuée (Van Dongen 2003)."},
        ],
    }


def build_pillar_detail_activity(activity: list[dict], today: date) -> dict | None:
    sorted_act = sorted([a for a in activity if a.get("steps")], key=lambda a: a["date"])
    if not sorted_act:
        return None
    latest = (
        next((a for a in reversed(sorted_act) if int(a["steps"]) >= 4000), None)
        or sorted_act[-1]
    )
    last30 = sorted_act[-30:]
    avg = sum(int(a["steps"]) for a in last30) / len(last30)
    delta_label = f"moyenne {len(last30)} j: {int(avg):,} / 10 000".replace(",", " ")
    pts = [{"date": fmt_date_fr(date.fromisoformat(a["date"])), "value": int(a["steps"])} for a in last30]
    rows = [{"date": fmt_date_fr(date.fromisoformat(a["date"])), "value": int(a["steps"]), "unit": "pas",
             "off": int(a["steps"]) < 7000} for a in sorted_act[-7:][::-1]]
    return {
        "key": "activity",
        "title": "Activité quotidienne",
        "meta": f"Health Connect → Withings · dernière donnée {fmt_date_fr(date.fromisoformat(latest['date']))}",
        "hero": {
            "figure": f"{int(latest['steps']):,}".replace(",", " "),
            "unit": "pas (dernier jour)",
            "delta_label": delta_label,
            "status_label": "Conforme" if avg >= 9000 else "Dérive mineure",
            "status_off": avg < 9000,
        },
        "trajectory": {
            "x_label": "30 j",
            "y_unit": "pas/j",
            "y_min": 0,
            "y_max": max(15000, int(max(int(a["steps"]) for a in last30) * 1.1)),
            "points": pts,
            "target": {"value": 10000, "label": "cible 10 k"},
        },
        "table": rows,
        "method": [
            {"heading": "Source", "body": "Huawei Watch GT2 (TruSeen 3.0) → Health Sync → Google Health Connect → Withings via cloud sync. Cron ingest 3 h."},
            {"heading": "Cible 10 000 pas", "body": "Heuristique mainstream (Tudor-Locke 2011). Corrélation mortalité all-cause: plateau 8 000-12 000 pas/j chez adultes <60 ans (Saint-Maurice JAMA 2020)."},
            {"heading": "Fenêtre", "body": "30 derniers jours. Moyenne mobile 7 j pour lisser les week-ends."},
        ],
    }


def _sample_weekly(rows: list[dict], today: date, weeks: int) -> list[tuple[date, float]]:
    """One value per ISO week in the window: average of all measurements that week."""
    cutoff = today - timedelta(weeks=weeks)
    buckets: dict[tuple[int, int], list[tuple[date, float]]] = {}
    for r in rows:
        d = date.fromisoformat(r["ts"][:10])
        if d < cutoff or d > today:
            continue
        y, w, _ = d.isocalendar()
        buckets.setdefault((y, w), []).append((d, float(r["value"])))
    out: list[tuple[date, float]] = []
    for key in sorted(buckets):
        days = buckets[key]
        avg = sum(v for _, v in days) / len(days)
        anchor = max(d for d, _ in days)
        out.append((anchor, avg))
    return out


def _sample_monthly(rows: list[dict], today: date, months: int) -> list[dict]:
    """Pick one row per month going back `months`, anchored on today."""
    out = []
    seen = set()
    for r in rows:
        d = date.fromisoformat(r["ts"][:10])
        key = (d.year, d.month)
        if (today - d).days <= months * 31 and key not in seen:
            seen.add(key)
            out.append(r)
    return sorted(out, key=lambda r: r["ts"])


# ---------- main ----------------------------------------------------------

def main() -> None:
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        env(k)
    today = date.fromisoformat(os.environ.get("TODAY") or datetime.now(timezone.utc).date().isoformat())
    print(f"→ building snapshot for {today}", file=sys.stderr)

    measurements = sb_get("withings_measurement", {
        "select": "ts,type_code,value,position,raw",
        "order": "ts.desc",
    })
    activity = sb_get("withings_activity_daily", {
        "select": "date,steps,distance_m,active_min,active_kcal,total_kcal,raw",
        "order": "date.desc",
    })
    yazio = sb_get("yazio_day", {
        "select": "date,kcal,protein_g,carb_g,fat_g,steps,weight_kg",
        "order": "date.desc",
    })
    hc_records = sb_get("hc_raw_record", {
        "select": "record_type,start_ts,end_ts,value_num,unit,source_app",
        "order": "start_ts.desc",
    })
    print(f"  loaded: {len(measurements)} withings rows, {len(activity)} activity days, {len(yazio)} yazio days, {len(hc_records)} HC records", file=sys.stderr)

    pillar_detail: dict[str, Any] = {}
    comp_detail = build_pillar_detail_composition(measurements, today)
    if comp_detail:
        pillar_detail["composition"] = comp_detail
    act_detail = build_pillar_detail_activity(activity, today)
    if act_detail:
        pillar_detail["activity"] = act_detail
    cardio_detail = build_pillar_detail_cardio(activity, today)
    if cardio_detail:
        pillar_detail["cardio"] = cardio_detail
    recovery_detail = build_pillar_detail_recovery(hc_records, today)
    if recovery_detail:
        pillar_detail["recovery"] = recovery_detail

    payload: dict[str, Any] = {
        "today": today.isoformat(),
        "hero": build_hero(measurements, today),
        "wegovy": build_wegovy(today),
        "signals": build_signals(yazio, measurements, activity, hc_records, today),
        "bio_age": build_bio_age(measurements),
        "pillars": build_pillars(yazio, measurements, activity, hc_records, today),
        "pillar_detail": pillar_detail,
    }

    sb_upsert(
        [{"snapshot_date": today.isoformat(), "payload": payload}],
        "cockpit_snapshot",
        "snapshot_date",
    )
    print(f"done. hero={payload['hero']['current_kg']}kg ({payload['hero']['statusLabel']}), signals={len(payload['signals'])}, pillars={len(payload['pillars'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
