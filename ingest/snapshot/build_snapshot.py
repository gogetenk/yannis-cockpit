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


def build_signals(yazio: list[dict], measurements: list[dict], activity: list[dict], today: date) -> list[dict]:
    """5 cross-source signals. Returns only those with usable data."""
    out: list[dict] = []

    # --- TDEE apparent (28d) ---
    win_start = today - timedelta(days=28)
    intake = [y for y in yazio if y.get("kcal") and date.fromisoformat(y["date"]) >= win_start and date.fromisoformat(y["date"]) <= today]
    wt_start = next((m for m in measurements if m["type_code"] == 1 and date.fromisoformat(m["ts"][:10]) <= win_start), None)
    wt_end = latest_weight_kg(measurements)
    if intake and wt_start and wt_end and len(intake) >= 7:
        avg_intake = sum(float(y["kcal"]) for y in intake) / len(intake)
        delta_kg = wt_end[0] - float(wt_start["value"])
        tdee = avg_intake + (delta_kg * 6500) / 28
        tdee_band = round(abs(tdee) * 0.08)
        out.append({
            "id": "tdee",
            "title": "TDEE apparent",
            "sub": f"{len(intake)} j · coeff 6 500 (Wegovy) · biais log ~20 %",
            "value": f"{int(round(tdee)):,}".replace(",", " "),
            "unit": f"± {tdee_band} kcal",
            "status": "ok",
            "status_label": "on track",
            "spark": _line_spark([float(y["kcal"]) for y in intake[-14:]], "sage"),
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


def build_pillars(yazio: list[dict], measurements: list[dict], activity: list[dict], today: date) -> list[dict]:
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

    # Activity: today's steps + 7 days bars
    if activity:
        sorted_act = sorted(activity, key=lambda a: a["date"])
        latest_steps = next((a for a in reversed(sorted_act) if a.get("steps")), None)
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

    # Cardio: VO2max if present
    vo2_series = sorted([m for m in measurements if m["type_code"] == 123], key=lambda m: m["ts"])
    if vo2_series:
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
        "select": "date,steps,distance_m,active_min,active_kcal,total_kcal",
        "order": "date.desc",
    })
    yazio = sb_get("yazio_day", {
        "select": "date,kcal,protein_g,carb_g,fat_g,steps,weight_kg",
        "order": "date.desc",
    })
    print(f"  loaded: {len(measurements)} withings rows, {len(activity)} activity days, {len(yazio)} yazio days", file=sys.stderr)

    payload: dict[str, Any] = {
        "today": today.isoformat(),
        "hero": build_hero(measurements, today),
        "wegovy": build_wegovy(today),
        "signals": build_signals(yazio, measurements, activity, today),
        "bio_age": build_bio_age(measurements),
        "pillars": build_pillars(yazio, measurements, activity, today),
    }

    sb_upsert(
        [{"snapshot_date": today.isoformat(), "payload": payload}],
        "cockpit_snapshot",
        "snapshot_date",
    )
    print(f"done. hero={payload['hero']['current_kg']}kg ({payload['hero']['statusLabel']}), signals={len(payload['signals'])}, pillars={len(payload['pillars'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
