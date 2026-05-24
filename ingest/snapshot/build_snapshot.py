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


CHRONO_AGE = 35
# VO2max via Uth-Sørensen-Overgaard 2004 (Eur J Appl Physiol 91:111):
#   VO2max ≈ 15.3 × HRmax / HRrest
# HRmax = 208 − 0.7 × age (Tanaka 2001 meta-analysis, more accurate than 220−age).
# Computed dynamically from huawei_daily.rest_hr_min trough; falls back to a
# conservative Huawei in-app value if no HR data.
VO2_MAX_FALLBACK = 45  # last manual Huawei reading 2026-05-23


def vo2max_uth(rest_hr_min: float, age: float = CHRONO_AGE) -> float:
    hr_max = 208 - 0.7 * age
    return round(15.3 * hr_max / rest_hr_min, 1)

WEGOVY_START_ISO = "2026-04-14"  # Yannis' first injection (J1).
WEGOVY_LADDER = [
    {"dose_mg": 0.25, "weeks": (0, 4)},
    {"dose_mg": 0.5,  "weeks": (4, 8)},
    {"dose_mg": 1.0,  "weeks": (8, 12)},
    {"dose_mg": 1.7,  "weeks": (12, 16)},
    {"dose_mg": 2.4,  "weeks": (16, None)},
]
TOLERANCE_KG = 0.8
# Gompertz fit refit against Wilding 2021 STEP-1 (semaglutide 2.4 mg, 68 wk).
# Cohort mean: -3% w4, -8% w16, -11.5% w28, -13.5% w40, -14.5% w52, -14.9% w68.
# Applied to Yannis' baseline 86.6 kg → asymptotic floor ≈ 74 kg, NOT 75.
# 75 kg is the user goal, reached around week 38 of the model (≈ early Jan 2027).
ASYMPTOTE_KG = 74.0   # STEP-1 plateau extrapolated to Yannis' baseline.
START_KG = 86.6       # Withings reading at J1.
GOMP_TAU_WEEKS = 20   # cohort time constant; matches STEP-1 mid-curve.
GOMP_SHAPE = 1.4
GOAL_KG = 75.0

# DEXA from HBG MC scan dated 2026-04-21 (Yannis age 35.1). T-scores by site.
# Pondération inspirée ISCD: rachis 40% (trabéculaire = marqueur précoce),
# col fémoral 30% (prédicteur fracture), hanche totale 15%, radius 15% (cortical).
# Conversion T-score → âge osseux: 1 SD ≈ 8 années (Kanis 2008 / FRAX ref).
DEXA = {
    "date": "2026-04-21",
    "tscores": {
        "spine_L1_L4": -1.3,            # ostéopénie légère, tirée par L1 (-2.2)
        "femoral_neck_avg": -0.65,      # (-0.8 G + -0.5 D) / 2, normal
        "total_hip_avg": -0.75,         # (-0.8 G + -0.7 D) / 2, normal
        "radius_total_avg": 0.9,        # (+0.7 G + +1.1 D) / 2, normal fort
    },
    "weights": {
        "spine_L1_L4": 0.40,
        "femoral_neck_avg": 0.30,
        "total_hip_avg": 0.15,
        "radius_total_avg": 0.15,
    },
    "years_per_sd": 8.0,                # Heuristic, NOT from Kanis 2008 (not
                                        # validated in young males). Treat as
                                        # rough age-equivalence, not clinical.
    "ref_age": 30,                      # Peak BMD age (young adult mean).
}

# DEXA total body composition (TBC, 2026-04-21). Gold standard for fat/lean.
# Used (a) directly when displaying body comp, (b) to derive a calibration
# delta vs Withings BIA same-day so we can correct future BIA readings.
DEXA_TBC = {
    "date": "2026-04-21",
    "weight_kg": 86.3,
    "fat_pct": 24.3,                    # total body fat %
    "fat_mass_kg": 20.07,
    "lean_mass_kg": 59.94,              # lean only (excl. BMC)
    "lean_plus_bmc_kg": 62.65,
    "asm_kg": 28.2,                     # appendicular skeletal muscle → SMI
    "vat_mass_g": 608,
    "vat_sat_ratio": 1.15,
    "trunk_fat_pct": 27.2,
    "android_fat_pct": 33.8,
    "gynoid_fat_pct": 31.3,
}

# Withings BIA same-day reading (2026-04-21): fat 20.43%, muscle 64.17 kg.
# DEXA − Withings = +3.87 pp on body fat (Withings underestimates in lean
# subjects, classical BIA artifact). Additive correction is more robust than
# multiplicative at low body fat. Applied to all Withings fat % readings.
BIA_FAT_OFFSET_PP = round(DEXA_TBC["fat_pct"] - 20.43, 2)  # = +3.87
BIA_REFERENCE_DATE = DEXA_TBC["date"]

# Per-segment correction coefficients (DEXA / Withings, computed on 2026-04-21).
# Withings BIA biases are NOT uniform across segments:
#   - Legs FAT massively underestimated (×1.55-1.61): impedance bias from
#     hydration gradient + dense lean leg mass.
#   - Trunk MUSCLE massively overestimated (×0.79): BIA conflates water +
#     organs with skeletal muscle.
# Withings type codes: 174 = fat_mass_segment, 175 = muscle_mass_segment.
# Position codes: 2=L-arm, 3=R-arm, 10=L-leg, 11=R-leg, 12=trunk.
BIA_SEGMENT_COEFFS = {
    174: {  # fat per segment
        2: 0.93, 3: 0.95,        # arms ≈ accurate (-5-7%)
        10: 1.55, 11: 1.61,      # legs ×1.55-1.61 (big underestimate)
        12: 0.97,                # trunk ≈ accurate (-3%)
    },
    175: {  # muscle per segment
        2: 0.89, 3: 0.99,        # arms ≈ accurate
        10: 0.94, 11: 0.94,      # legs slightly overestimated
        12: 0.79,                # trunk -21% (BIA counts water/organs as muscle)
    },
}


def withings_fat_pct_corrected(raw_pct: float) -> float:
    """Apply DEXA-anchored additive offset to Withings BIA global fat %."""
    return raw_pct + BIA_FAT_OFFSET_PP


def withings_segment_corrected(raw_kg: float, type_code: int, position: int) -> float:
    """Apply per-segment DEXA-anchored coefficient to a Withings BIA segment."""
    coef = BIA_SEGMENT_COEFFS.get(type_code, {}).get(position, 1.0)
    return raw_kg * coef
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


def weight_projected(t_weeks: float, tau: float = 18) -> float:
    """Personal projection curve. Tau defaults to STEP-1 cohort mid value (18)
    but is normally re-fit to actual trajectory via fit_personal_tau()."""
    return ASYMPTOTE_KG + (START_KG - ASYMPTOTE_KG) * math.exp(-math.pow(t_weeks / tau, GOMP_SHAPE))


def fit_personal_tau(current_kg: float, today_week: float) -> float:
    """Solve Gompertz for tau given (today_week, current_kg).
    Returns the cohort tau (18) if today_week too small or current_kg already ≤ asymptote."""
    if today_week < 2:
        return 18.0
    ratio = (current_kg - ASYMPTOTE_KG) / (START_KG - ASYMPTOTE_KG)
    if ratio <= 0 or ratio >= 1:
        return 18.0
    return today_week / math.pow(math.log(1 / ratio), 1 / GOMP_SHAPE)


def smoothed_current_kg(measurements: list[dict], today: date, days: int = 7) -> float | None:
    """7-day rolling average of weight, to dampen daily hydration noise."""
    cutoff = today - timedelta(days=days)
    weights = [
        float(m["value"]) for m in measurements
        if m["type_code"] == 1 and date.fromisoformat(m["ts"][:10]) >= cutoff
    ]
    return sum(weights) / len(weights) if weights else None


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
    # ETA: re-fit tau on the smoothed current weight so the projection
    # reflects YOUR actual trajectory, not the cohort mean. Use a 7-day
    # rolling average to absorb daily hydration noise.
    smoothed = smoothed_current_kg(measurements, today, 7) or current_kg
    personal_tau = fit_personal_tau(smoothed, today_week)
    eta_week = None
    for w in [today_week + 0.5 * i for i in range(0, 400)]:
        if weight_projected(w, personal_tau) <= GOAL_KG:
            eta_week = w
            break
    eta_date = start + timedelta(days=int(eta_week * 7)) if eta_week else (start + timedelta(weeks=80))
    return {
        "status": status_key,
        "statusLabel": status_label,
        "current_kg": round(current_kg, 1),
        "ideal_kg": round(ideal, 1),
        "delta_kg": round(delta, 1),
        "tolerance_kg": TOLERANCE_KG,
        "start_date": WEGOVY_START_ISO,
        "today_week": round(today_week, 1),
        "range": {"kg_min": 73, "kg_max": 87, "week_min": 0, "week_max": 52},
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


def build_signals(yazio: list[dict], measurements: list[dict], activity: list[dict], hc_records: list[dict], today: date, huawei_daily: list[dict] | None = None) -> list[dict]:
    """5 cross-source signals. Returns only those with usable data."""
    out: list[dict] = []

    # --- Déficit énergétique observable (28d) ---
    # Pivot from naive TDEE (biased by sparse intake logging) to a more honest
    # signal: the deficit implied purely by weight change. Independent of how
    # many days the user logged. Uses 6500 kcal/kg coefficient (within the
    # ~5400-6000 range for body weight loss per Hall 2011 Lancet dynamic model;
    # 7700 is for pure adipose only).
    win_start = today - timedelta(days=28)
    wt_start = next(
        (m for m in measurements if m["type_code"] == 1 and date.fromisoformat(m["ts"][:10]) <= win_start),
        None,
    )
    wt_end = latest_weight_kg(measurements)
    if wt_start and wt_end:
        delta_kg = wt_end[0] - float(wt_start["value"])
        deficit_per_day = -(delta_kg * 6500) / 28  # positive when losing
        watch = deficit_per_day > 800  # >800 kcal/j = perte trop rapide
        title = "Déficit calorique" if deficit_per_day >= 0 else "Surplus calorique"
        label = "trop rapide" if watch else ("perte en cours" if deficit_per_day > 100 else "stable")
        out.append({
            "id": "deficit",
            "title": title,
            "sub": "",
            "value": f"{abs(int(round(deficit_per_day))):,}".replace(",", " "),
            "unit": "kcal/j",
            "status": "watch" if watch else "ok",
            "status_label": label,
            "spark": {"kind": "line", "color": "sage", "points": []},
        })

    # --- Proteins / LBM ---
    # yazio is sorted date.desc by sb_get, so we filter by date (NOT slice).
    # Skip zero/partial-log days (<30 g) — they're days not actually logged,
    # not days of fasting.
    lbm_row = next((m for m in measurements if m["type_code"] == 5 and (m.get("position") in (None, 0, 7))), None)
    proteins = [
        float(y["protein_g"]) for y in yazio
        if y.get("protein_g") is not None
        and float(y["protein_g"]) > 30
        and win_start <= date.fromisoformat(y["date"]) <= today
    ]
    if lbm_row and proteins:
        lbm = float(lbm_row["value"])
        avg_protein = sum(proteins) / len(proteins)
        target_g = round(lbm * 2.0)  # 2.0 g/kg LBM target
        watch = avg_protein < target_g * 0.85
        out.append({
            "id": "protein",
            "title": "Protéines",
            "sub": f"cible {target_g} g/j",
            "value": str(int(round(avg_protein))),
            "unit": "g/j",
            "status": "watch" if watch else "ok",
            "status_label": "à surveiller" if watch else "conforme",
            "spark": _bar_spark(proteins[-13:], "ambre" if watch else "sage"),
        })

    # Wegovy response z-score removed: hero already conveys "X kg en avance/retard
    # vs cible idéale", which is the human-readable form of the same z.

    # --- Sleep debt vs optimum 7.5h (Hirshkowitz NSF 2015, Walker 2017) ---
    sleep_pts = _sleep_minutes_per_day(hc_records, today, 14)
    if len(sleep_pts) >= 3:
        last7 = sleep_pts[-7:]
        avg_min = sum(v for _, v in last7) / len(last7)
        deficit = (450 - avg_min) * len(last7) / 60  # hours under 7.5h optimum
        watch = avg_min < 400  # <6h40 = alerte
        out.append({
            "id": "sleep",
            "title": "Dette sommeil",
            "sub": "optimum 7 h 30",
            "value": f"{'+' if deficit < 0 else '−'}{abs(int(deficit))} h",
            "unit": f"/ {len(last7)} j",
            "status": "watch" if watch else "ok",
            "status_label": "à surveiller" if watch else "conforme",
            "spark": _bar_spark([v for _, v in last7], "ambre" if watch else "sage"),
        })

    # --- Steps / activity ---
    recent_act = [a for a in activity if a.get("steps") and date.fromisoformat(a["date"]) >= today - timedelta(days=28)]
    if recent_act:
        avg_steps = sum(int(a["steps"]) for a in recent_act) / len(recent_act)
        watch = avg_steps < 9000
        out.append({
            "id": "activity",
            "title": "Activité",
            "sub": "cible 10 k/j",
            "value": f"{int(round(avg_steps)):,}".replace(",", " "),
            "unit": "pas/j",
            "status": "watch" if watch else "ok",
            "status_label": "à surveiller" if watch else "conforme",
            "spark": _bar_spark([int(a["steps"]) for a in recent_act[-14:]], "ambre" if watch else "sage"),
        })

    # --- Stress chronique (Huawei TruSeen) ---
    # Rolling 7d vs baseline 90d. Threshold +15 % sustained = drift.
    if huawei_daily:
        cutoff_7 = today - timedelta(days=7)
        cutoff_90 = today - timedelta(days=90)
        s7 = [float(r["stress_avg"]) for r in huawei_daily
              if r.get("stress_avg") is not None
              and date.fromisoformat(r["date"]) >= cutoff_7]
        s90 = [float(r["stress_avg"]) for r in huawei_daily
               if r.get("stress_avg") is not None
               and date.fromisoformat(r["date"]) >= cutoff_90]
        if len(s7) >= 7 and len(s90) >= 30:
            avg7 = sum(s7) / len(s7)
            avg90 = sum(s90) / len(s90)
            drift = (avg7 - avg90) / avg90 if avg90 > 0 else 0
            watch = drift > 0.15
            out.append({
                "id": "stress_chronic",
                "title": "Stress chronique",
                "sub": "vs baseline 90 j",
                "value": str(int(round(avg7))),
                "unit": "/100",
                "status": "watch" if watch else "ok",
                "status_label": "à surveiller" if watch else "conforme",
                "spark": _bar_spark([int(round(v)) for v in s7[-14:]], "ambre" if watch else "sage"),
            })

    return out


def build_ai_brief(payload: dict, full_context: dict | None = None) -> str | None:
    """Call Claude Haiku 4.5 with the FULL snapshot payload + raw data context
    and ask for a 15-30 word actionable French analysis. Returns None if no
    API key configured.

    The model has access to everything in `payload` (hero, signals, biology,
    bio_age, pillars, wegovy, action_today) plus a compact summary of the
    underlying raw measurements/labs in `full_context` so it can corroborate
    or contradict the dashboard-level conclusions.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("  (skipping ai_brief: ANTHROPIC_API_KEY not set)", file=sys.stderr)
        return None
    try:
        import anthropic
    except ImportError:
        print("  (skipping ai_brief: anthropic SDK not installed)", file=sys.stderr)
        return None

    # Pre-compute explicit facts so the model doesn't have to derive them
    # (and can't get them wrong). Pass status of every signal as "conforme"
    # or "à surveiller" verbatim.
    hero = payload.get("hero", {})
    wegovy = payload.get("wegovy", {})
    signals = payload.get("signals", [])
    facts = {
        "poids_actuel_kg": hero.get("current_kg"),
        "poids_initial_kg": 86.6,
        "kg_perdus_depuis_J1": round(86.6 - float(hero.get("current_kg") or 86.6), 1),
        "status_hero": hero.get("statusLabel"),
        "ecart_vs_courbe_ideale_kg": hero.get("delta_kg"),
        "wegovy_jour": wegovy.get("day_since_start"),
        "wegovy_dose_actuelle_mg": wegovy.get("current_dose_mg"),
        "wegovy_prochaine_titration_mg": wegovy.get("next_dose_mg"),
        "wegovy_prochaine_titration_dans_sem": wegovy.get("next_in_weeks"),
        "signaux": [
            {
                "nom": s.get("title"),
                "valeur": f"{s.get('value')} {s.get('unit')}".strip(),
                "statut": s.get("status_label"),
                "conforme": s.get("status") == "ok",
            }
            for s in signals
        ],
        "biology": payload.get("biology"),
    }

    system_prompt = (
        "Tu es l'analyste du cockpit santé de Yannis (35 ans, homme, 173 cm). "
        "À chaque appel, tu produis UNE phrase française de 15 à 30 mots, "
        "factuelle, ancrée DANS LES FAITS fournis ci-dessous. "
        "\n\nRÈGLES STRICTES:\n"
        "- Tu n'inventes JAMAIS de chiffres, dates, doses, ou plans.\n"
        "- Si tous les signaux sont 'conforme', dis-le sans suggérer d'action.\n"
        "- Si un signal est 'à surveiller', cite-le explicitement avec son nom.\n"
        "- N'utilise QUE les valeurs présentes dans le bloc facts.\n"
        "- Pas d'em-dash. Pas de jargon (LBM, z-score, SD). Pas de cheerleading.\n"
        "- Pas de 'vous devriez', 'il faut', 'augmenter à X'. Reste descriptif.\n"
        "- Pas de préambule, pas de guillemets, juste la phrase.\n"
    )

    user_msg = json.dumps({
        "facts": facts,
        "raw_context_supplementaire": full_context or {},
    }, ensure_ascii=False, default=str)

    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text.strip() if resp.content else ""
        # Strip leading/trailing quotes the model sometimes adds
        text = text.strip('"\u201c\u201d').strip()
        return text or None
    except Exception as e:
        print(f"  (ai_brief failed: {e})", file=sys.stderr)
        return None


def build_action_today(signals: list[dict]) -> str | None:
    """Pick the single most actionable signal and turn it into a 1-line action.
    Returns None if everything is conforme."""
    # Prefer watch>watch but pick a specific intervention.
    by_id = {s["id"]: s for s in signals}
    sleep = by_id.get("sleep")
    if sleep and sleep["status"] == "watch":
        return "Couche-toi 1 h plus tôt ce soir"
    protein = by_id.get("protein")
    if protein and protein["status"] == "watch":
        return "Ajoute ~30 g de protéines au dîner (skyr, œufs, whey)"
    activity = by_id.get("activity")
    if activity and activity["status"] == "watch":
        return "Marche 30 min supplémentaires aujourd'hui"
    deficit = by_id.get("deficit")
    if deficit and deficit["status"] == "watch":
        return "Repas plus copieux : déficit calorique trop rapide"
    return None


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


def latest_lab_panel(panels: list[dict], results: list[dict]) -> tuple[dict, dict] | None:
    """Return (panel, {marker_code: result}) for the most recent panel, or None."""
    if not panels:
        return None
    latest = max(panels, key=lambda p: p["collected_at"])
    by_marker = {r["marker_code"]: r for r in results if r["panel_id"] == latest["id"]}
    return latest, by_marker


def phenoage_levine(markers: dict, chrono_yr: float) -> float | None:
    """Levine PhenoAge (Aging 2018, NHANES + UK Biobank validated, n>10k).

    Requires 9 biomarkers. Returns None if any is missing.
    Coefficients from Levine ML et al., Aging (Albany NY) 2018; 10(4):573-91.
    """
    def get(code: str) -> float | None:
        r = markers.get(code)
        return float(r["value_num"]) if r and r.get("value_num") is not None else None

    alb_g_dL = get("Albumin")
    creat_mg_dL = get("Creat")
    glu_mg_dL = get("Glu")
    crp_mg_L = get("hsCRP")
    lymph_pct = get("Lymph_pct")
    mcv = get("MCV")
    rdw = get("RDW")
    alp = get("ALP")
    wbc = get("WBC")
    if None in (alb_g_dL, creat_mg_dL, glu_mg_dL, crp_mg_L, lymph_pct, mcv, rdw, alp, wbc):
        return None

    # Unit conversions to Levine's input units.
    albumin_g_L = alb_g_dL * 10
    creat_umol_L = creat_mg_dL * 88.4
    glu_mmol_L = glu_mg_dL / 18.018
    crp_mg_dL = crp_mg_L / 10
    ln_crp = math.log(max(crp_mg_dL, 1e-4))

    xb = (-19.907
          - 0.0336 * albumin_g_L
          + 0.0095 * creat_umol_L
          + 0.1953 * glu_mmol_L
          + 0.0954 * ln_crp
          - 0.0120 * lymph_pct
          + 0.0268 * mcv
          + 0.3306 * rdw
          + 0.00188 * alp
          + 0.0554 * wbc
          + 0.0804 * chrono_yr)
    M = 1 - math.exp(-1.51714 * math.exp(xb) / 0.0076927)
    return 141.50 + math.log(-0.00553 * math.log(max(1 - M, 1e-9))) / 0.09165


def avg_bp_28d(measurements: list[dict], today: date) -> tuple[float | None, float | None]:
    """Average SBP (type 10) and DBP (type 9) from Withings BPM over 28 days."""
    cutoff = today - timedelta(days=28)
    sbp = [float(m["value"]) for m in measurements
           if m["type_code"] == 10 and date.fromisoformat(m["ts"][:10]) >= cutoff]
    dbp = [float(m["value"]) for m in measurements
           if m["type_code"] == 9 and date.fromisoformat(m["ts"][:10]) >= cutoff]
    return (
        sum(sbp) / len(sbp) if sbp else None,
        sum(dbp) / len(dbp) if dbp else None,
    )


def prevent_30y_total_cvd(markers: dict, sbp: float | None, dbp: float | None,
                          age_yr: float = 35, bmi: float = 28.8,
                          on_bp_treatment: bool = False, on_statin: bool = False) -> float:
    """Heuristic 30-year total CVD risk, MALE.

    NOT the real PREVENT 2023 equation. The actual PREVENT coefficients
    (Khan et al. Circulation 2024;149:430-449) are public in the
    eAppendix and should ideally replace this. This function is a
    transparent multiplicative heuristic anchored on a reference 35M
    optimal profile, useful for direction but NOT for clinical decisions.

    To upgrade: download the Khan 2024 eAppendix Tables S6 (30y total
    CVD, male) and replace coefficient block with the published Cox
    survival function.
    """
    def get(code: str) -> float | None:
        r = markers.get(code)
        return float(r["value_num"]) if r and r.get("value_num") is not None else None

    non_hdl = get("NonHDL") or 130
    hdl = get("HDL") or 50
    egfr = get("eGFR") or 100
    hba1c = get("HbA1c") or 5.0
    glu = get("Glu") or 90
    uacr_marker = markers.get("uAlb")
    uacr = None if uacr_marker is None or uacr_marker.get("value_num") is None else float(uacr_marker["value_num"])
    diabetes = hba1c >= 6.5 or glu >= 126
    smoking = False  # à wire si on l'a un jour

    mult = 1.0
    # Age (centered at 35; +10 yr ≈ 1.5×)
    mult *= 1.5 ** ((age_yr - 35) / 10)
    # Non-HDL cholesterol (ref 130 mg/dL)
    if non_hdl > 130:
        mult *= 1.3 ** ((non_hdl - 130) / 30)
    # HDL (ref 50; lower = worse)
    if hdl < 50:
        mult *= 1.2 ** ((50 - hdl) / 10)
    # SBP (ref 110 mmHg)
    if sbp and sbp > 110:
        mult *= 1.5 ** ((sbp - 110) / 20)
    # Diabetes
    if diabetes:
        mult *= 2.5
    # Smoking
    if smoking:
        mult *= 2.0
    # eGFR penalty <60 mL/min
    if egfr < 60:
        mult *= 1.7
    # UACR (urine microalbumin) ≥30 mg/g
    if uacr is not None and uacr >= 30:
        mult *= 1.5
    # BMI (ref 25)
    if bmi > 25:
        mult *= 1.1 ** ((bmi - 25) / 5)
    # Statin reduces non-HDL effect
    if on_statin:
        mult *= 0.85
    # BP treatment doesn't reduce risk in PREVENT (kept as marker of HTA)
    if on_bp_treatment:
        mult *= 1.1

    return min(99.0, round(7.5 * mult, 1))


def prevent_band(pct: float) -> tuple[str, str]:
    """AHA risk banding (applied here to 30y for simplicity)."""
    if pct < 5: return "low", "faible"
    if pct < 7.5: return "borderline", "borderline"
    if pct < 20: return "intermediate", "intermédiaire"
    return "high", "élevé"


def lifetime_cv_risk(markers: dict, age_yr: float, bmi: float | None = None) -> tuple[int, str]:  # type: ignore[override]
    """Backwards-compat wrapper; full version below returns (pct, label, driver)."""
    pct, label, _ = lifetime_cv_risk_full(markers, age_yr, bmi, None, None)
    return pct, label


def lifetime_cv_risk_full(markers: dict, age_yr: float, bmi: float | None = None,
                          sbp: float | None = None, dbp: float | None = None) -> tuple[int, str, str | None]:
    """ACC/AHA Lifetime CV Risk for adults <50 (Lloyd-Jones 2006, Berry 2012
    NEJM). Stratified into 5 categories based on classic risk factors at age
    of assessment. Returns (pct_risk, category_label).

    Categories (male, lifetime to age 80):
      all_optimal: chol<180 + BP<120/80 + no DM + no smoking      → 5%
      >=1_not_optimal: any in suboptimal range                    → 36%
      >=1_elevated: any chol 200-239 OR BP 140-159/90-99          → 39%
      >=1_major: chol>=240 OR BP>=160/100 OR DM OR smoker         → 50%
      >=2_major: two or more major risk factors                   → 69%
    """
    def get(code: str) -> float | None:
        r = markers.get(code)
        return float(r["value_num"]) if r and r.get("value_num") is not None else None

    chol = get("CholT")
    hba1c = get("HbA1c")
    glu = get("Glu")
    non_smoker = True
    diabetes = (hba1c is not None and hba1c >= 6.5) or (glu is not None and glu >= 126)

    major: list[str] = []
    elevated: list[str] = []
    not_optimal: list[str] = []
    if chol is not None:
        if chol >= 240: major.append(f"cholestérol {int(chol)} mg/dL")
        elif chol >= 200: elevated.append(f"cholestérol {int(chol)} mg/dL")
        elif chol >= 180: not_optimal.append(f"cholestérol {int(chol)} mg/dL")
    # SBP categories (AHA 2017): <120 optimal; 120-129 elevated; 130-139 stage 1;
    # 140-159 stage 1 → elevated; ≥160 major. DBP <80 optimal; 80-89 elevated; ≥100 major.
    # ACC/AHA 2002 thresholds (matching Berry 2012 model calibration):
    # SBP <120 optimal; 120-139 sub-optimal; 140-159 elevated; ≥160 major.
    if sbp is not None:
        if sbp >= 160: major.append(f"TA {int(sbp)} mmHg")
        elif sbp >= 140: elevated.append(f"TA {int(sbp)} mmHg")
        elif sbp >= 120: not_optimal.append(f"TA {int(sbp)} mmHg")
    if dbp is not None:
        if dbp >= 100: major.append(f"TA diastolique {int(dbp)}")
        elif dbp >= 90: elevated.append(f"TA diastolique {int(dbp)}")
        elif dbp >= 80: not_optimal.append(f"TA diastolique {int(dbp)}")
    if diabetes: major.append("diabète")
    if not non_smoker: major.append("tabac")

    # Berry NEJM 2012 Table 2, men, lifetime risk to age 80 from index age 45.
    if len(major) >= 2:
        return 69, "≥2 facteurs majeurs", " + ".join(major[:2])
    if len(major) >= 1:
        return 46, "facteur majeur", major[0]
    if elevated:
        return 36, "facteur élevé", elevated[0]
    if not_optimal:
        return 6, "facteur sous-optimal", not_optimal[0]
    return 1, "tous optimaux", None


def build_bio_age(measurements: list[dict], activity: list[dict] | None = None, labs: tuple | None = None, hc_records: list[dict] | None = None, huawei_daily: list[dict] | None = None, today: date | None = None) -> dict:
    """Bio age composite from what we can actually measure. Blood is held at
    chrono since labs are not ingested yet. Each subage rounds to nearest yr."""
    chrono = 35
    today = today or date.today()
    # Cardio: prefer VO2max Huawei (user-reported) via Wier-style age-equivalence:
    #   cardio_age = chrono - clamp(round((VO2max - 40)/2), -10, +10).
    # Honest, conservative: VO2max 45 at age 35 → cardio age 33.
    # Else: HR repos source priority huawei_daily.rest_hr_min last 30d > HC last
    # 30d > Withings/Huawei via activity (legacy) > chrono fallback.
    vo2 = next((m for m in measurements if m["type_code"] == 123), None)
    vo2_value: float | None = None
    if vo2:
        vo2_value = float(vo2["value"])
    elif huawei_daily and (hr_min := _huawei_rest_hr_min_30d(huawei_daily, today)) is not None:
        vo2_value = vo2max_uth(hr_min, chrono)
    elif VO2_MAX_FALLBACK:
        vo2_value = float(VO2_MAX_FALLBACK)
    if vo2_value is not None:
        delta = max(-10, min(10, int(round((vo2_value - 40) / 2))))
        cardio_age = max(20, chrono - delta)
    elif hc_records and (hr_hc := _avg_hr_from_hc(hc_records, today)) is not None:
        cardio_age = max(20, min(60, int(round(25 + (hr_hc - 50) * 1.0))))
    elif activity:
        hr_min_recent = sorted(
            [(a["date"], int(a["raw"]["hr_min"]))
             for a in activity
             if a.get("raw") and isinstance(a["raw"], dict) and a["raw"].get("hr_min")
             and int(a["raw"]["hr_min"]) > 30],
            key=lambda x: x[0],
        )[-7:]
        if hr_min_recent:
            avg_hr = sum(v for _, v in hr_min_recent) / len(hr_min_recent)
            cardio_age = max(20, min(60, int(round(25 + (avg_hr - 50) * 1.0))))
        else:
            cardio_age = chrono
    else:
        cardio_age = chrono
    # SBP penalty on cardio age: heuristic ~+3 years per +10 mmHg over 120
    # (empirical, derived from D'Agostino 2008 general CVD score; the exact
    # vascular-age conversion is not in the paper).
    avg_sbp_for_cardio = None
    if measurements:
        s_list = [float(m["value"]) for m in measurements if m["type_code"] == 10]
        if s_list:
            avg_sbp_for_cardio = sum(s_list[-28:]) / len(s_list[-28:])
    if avg_sbp_for_cardio and avg_sbp_for_cardio > 120:
        cardio_age = min(60, cardio_age + round((avg_sbp_for_cardio - 120) / 10 * 3))

    # Composition age: HEURISTIC. Anchored on DEXA-calibrated fat %. Reference
    # 18 % at age 30 for males is loosely based on Gallagher 2000 healthy band;
    # the linear "+2 pp = +1 yr" is NOT published, just a rough scaling.
    fat_ratio = next((m for m in measurements if m["type_code"] == 6 and (m.get("position") in (None, 0, 7))), None)
    fat_pct_corrected = withings_fat_pct_corrected(float(fat_ratio["value"])) if fat_ratio else None
    composition_age = chrono + int(round((fat_pct_corrected - 18) / 2)) if fat_pct_corrected else chrono

    # Skeleton: weighted bone age from DEXA T-scores (HBG MC scan 2026-04-21).
    # bone_age = ref_age + (-T_weighted) * years_per_SD
    # Negative T = lower BMD vs young adult mean = older bone equivalent.
    weighted_t = sum(DEXA["tscores"][k] * DEXA["weights"][k] for k in DEXA["tscores"])
    skeleton_age = int(round(DEXA["ref_age"] + (-weighted_t) * DEXA["years_per_sd"]))

    # Blood: PhenoAge Levine 2018 if labs available, else off.
    blood_age: int | None = None
    if labs:
        _, markers = labs
        pa = phenoage_levine(markers, chrono + 0.2)
        if pa is not None:
            blood_age = int(round(pa))

    measured = [cardio_age, composition_age, skeleton_age]
    if blood_age is not None:
        measured.append(blood_age)
    composite = round(sum(measured) / len(measured))

    return {
        "composite": composite,
        "chrono": chrono,
        "delta_vs_chrono": composite - chrono,
        "subages": [
            {"key": "cardio", "label": "Cardio", "value": cardio_age},
            {"key": "blood", "label": "Sang", "value": blood_age if blood_age is not None else chrono, "off": blood_age is None},
            {"key": "composition", "label": "Composition", "value": composition_age, "off": composition_age > chrono + 1},
            {"key": "skeleton", "label": "Squelette", "value": skeleton_age, "off": skeleton_age > chrono + 5},
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


def _huawei_5y_summary(huawei_daily: list[dict], today: date) -> dict:
    """Compact summary of the Huawei 5-y history for the LLM context."""
    cutoff_30 = today - timedelta(days=30)
    cutoff_7 = today - timedelta(days=7)
    cutoff_90 = today - timedelta(days=90)
    rest_hr_30 = [float(r["rest_hr_avg"]) for r in huawei_daily
                  if r.get("rest_hr_avg") is not None
                  and date.fromisoformat(r["date"]) >= cutoff_30]
    rest_hr_all = [float(r["rest_hr_avg"]) for r in huawei_daily
                   if r.get("rest_hr_avg") is not None]
    stress_7 = [float(r["stress_avg"]) for r in huawei_daily
                if r.get("stress_avg") is not None
                and date.fromisoformat(r["date"]) >= cutoff_7]
    stress_90 = [float(r["stress_avg"]) for r in huawei_daily
                 if r.get("stress_avg") is not None
                 and date.fromisoformat(r["date"]) >= cutoff_90]
    deep_pct_30 = []
    for r in huawei_daily:
        if r.get("sleep_total_min") and r.get("sleep_deep_min") is not None \
                and float(r["sleep_total_min"]) > 0 \
                and date.fromisoformat(r["date"]) >= cutoff_30:
            deep_pct_30.append(float(r["sleep_deep_min"]) / float(r["sleep_total_min"]) * 100)
    dates = sorted(r["date"] for r in huawei_daily if r.get("date"))
    years = round((date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days / 365.25, 1) if dates else 0
    return {
        "years_of_data": years,
        "current_rest_hr": round(sum(rest_hr_30) / len(rest_hr_30), 1) if rest_hr_30 else None,
        "all_time_rest_hr_avg": round(sum(rest_hr_all) / len(rest_hr_all), 1) if rest_hr_all else None,
        "stress_chronic_now_7d": round(sum(stress_7) / len(stress_7), 1) if stress_7 else None,
        "stress_baseline_90d": round(sum(stress_90) / len(stress_90), 1) if stress_90 else None,
        "sleep_deep_pct_30d": round(sum(deep_pct_30) / len(deep_pct_30), 1) if deep_pct_30 else None,
        "vo2max_uth": vo2max_uth(min(rest_hr_30), CHRONO_AGE) if rest_hr_30 else None,
        "vo2max_huawei_last_manual": VO2_MAX_FALLBACK,
    }


def _huawei_rest_hr_min_30d(huawei_daily: list[dict], today: date) -> float | None:
    """Min of daily rest_hr_min over last 30 days (Huawei). The trough = real
    resting HR; nightly minima fluctuate so we take the lowest of the window."""
    cutoff = today - timedelta(days=30)
    vals = [
        float(r["rest_hr_min"]) for r in huawei_daily
        if r.get("rest_hr_min") is not None
        and date.fromisoformat(r["date"]) >= cutoff
    ]
    return min(vals) if vals else None


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
    """Per-day NIGHT sleep minutes from sleep_session records.

    Health Connect receives the same night from multiple source apps (Withings,
    Health Sync, Google Fit). Dedup by (start_ts, end_ts) to avoid summing
    duplicates. Daytime naps (<4 h) are filtered so they don't inflate the
    night total when bucketed on the same wake day.
    """
    seen: set[tuple[str, str]] = set()
    by_day: dict[date, float] = {}
    for r in hc_records:
        if r["record_type"] != "sleep_session" or r.get("value_num") is None:
            continue
        mins = float(r["value_num"])
        if mins < 240:  # skip naps
            continue
        key = (r["start_ts"], r.get("end_ts") or "")
        if key in seen:
            continue
        seen.add(key)
        d_str = (r.get("end_ts") or r["start_ts"])[:10]
        d = date.fromisoformat(d_str)
        if d < today - timedelta(days=days) or d > today:
            continue
        # Take MAX rather than sum in case the same night gets logged with
        # different durations across sources (e.g. one trims wake-up time).
        by_day[d] = max(by_day.get(d, 0), mins)
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
            v = withings_fat_pct_corrected(float(m["value"]))
            # map 14-30% MG → y 12..72
            y = round(12 + ((30 - v) / 16) * 60, 1)
            y = max(8, min(72, y))
            pts.append([x, y])
        pillars.append({
            "key": "composition",
            "label": "Composition",
            "meta": fmt_date_fr(latest_date),
            "figure": fmt_num(withings_fat_pct_corrected(float(latest_bf["value"])), 1),
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
        latest_v = withings_segment_corrected(float(series[-1]["value"]), type_code, pos)
        first_v = withings_segment_corrected(float(series[0]["value"]), type_code, pos)
        delta = latest_v - first_v
        pts = [{"date": fmt_date_fr(date.fromisoformat(r["ts"][:10])),
                "value": round(withings_segment_corrected(float(r["value"]), type_code, pos), 2)} for r in series]
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
    latest_v = withings_fat_pct_corrected(float(latest["value"]))
    latest_d = date.fromisoformat(latest["ts"][:10])
    weekly = _sample_weekly(fat, today, 104)
    pts = [
        {"date": fmt_date_fr(d), "value": round(withings_fat_pct_corrected(v), 1)}
        for d, v in weekly
    ]
    six_m_ago = next((r for r in fat if (today - date.fromisoformat(r["ts"][:10])).days >= 180), None)
    delta = None
    if six_m_ago:
        delta = round(latest_v - withings_fat_pct_corrected(float(six_m_ago["value"])), 1)
    rows: list[dict] = []
    for m in fat[-8:][::-1]:
        d = date.fromisoformat(m["ts"][:10])
        rows.append({"date": fmt_date_fr(d), "value": round(withings_fat_pct_corrected(float(m["value"])), 1), "unit": "% MG"})
    return {
        "key": "composition",
        "title": "Composition corporelle",
        "meta": f"Withings (calibré DEXA {BIA_REFERENCE_DATE}) · dernière mesure {fmt_date_fr(latest_d)}",
        "hero": {
            "figure": fmt_num(latest_v, 1),
            "unit": "% MG",
            "delta_label": (f"{'+' if delta >= 0 else '−'}{abs(delta)} pts vs 6 mois" if delta is not None else None),
            "status_label": "Conforme" if latest_v <= 20 else "Dérive mineure" if latest_v <= 24 else "Dérive notable",
            "status_off": latest_v > 20,
        },
        "trajectory": {
            "x_label": "12 mois",
            "y_unit": "% MG",
            "y_min": 14,
            "y_max": 32,
            "points": pts,
            "target": {"value": 18, "label": "cible 18 %"},
        },
        "table": rows,
        "subs": _segment_subs(measurements, 175, "kg", "muscle") + _segment_subs(measurements, 174, "kg", "fat"),
        "method": [
            {"heading": "Source", "body": f"Withings Body Scan (BIA segmentale 8 électrodes, 50 kHz). Mesures Withings calibrées par offset +{BIA_FAT_OFFSET_PP} pp dérivé du DEXA total body HBG MC du {BIA_REFERENCE_DATE} (BIA Withings sous-estime systématiquement la MG chez les sujets minces, artefact classique)."},
            {"heading": "DEXA référence", "body": f"DEXA TBC ({BIA_REFERENCE_DATE}) : MG totale {DEXA_TBC['fat_pct']} %, masse maigre {DEXA_TBC['lean_mass_kg']} kg, ASM {DEXA_TBC['asm_kg']} kg (SMI 9,4 — normal), VAT {DEXA_TBC['vat_mass_g']} g, ratio VAT/SAT {DEXA_TBC['vat_sat_ratio']}."},
            {"heading": "Cible", "body": "18 % long terme (catégorie 'athletic' ACSM cohorte 30-39 ans, post-correction DEXA). Bande conforme jusqu'à 20 %."},
        ],
    }


def build_pillar_detail_cardio(activity: list[dict], today: date, huawei_daily: list[dict] | None = None) -> dict | None:
    """HR repos. Source priority: huawei_daily.rest_hr_avg (5 y history) >
    Withings activity.raw.hr_min (Huawei via HC, ~30 j window)."""
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
    # 24 months trajectory from huawei_daily.rest_hr_avg (monthly average),
    # falling back to last 90 days from Withings/HC otherwise.
    pts_24m: list[dict] = []
    if huawei_daily:
        cutoff_24m = today - timedelta(days=730)
        buckets: dict[str, list[float]] = {}
        for r in huawei_daily:
            if r.get("rest_hr_avg") is None:
                continue
            d = date.fromisoformat(r["date"])
            if d < cutoff_24m:
                continue
            key = f"{d.year:04d}-{d.month:02d}"
            buckets.setdefault(key, []).append(float(r["rest_hr_avg"]))
        for key in sorted(buckets):
            yr, mo = key.split("-")
            label = MONTHS_FR[int(mo) - 1].upper() + " '" + yr[2:]
            pts_24m.append({"date": label, "value": int(round(sum(buckets[key]) / len(buckets[key])))})
    # 90 days trajectory (legacy fallback)
    pts = pts_24m if pts_24m else [{"date": fmt_date_fr(date.fromisoformat(d)), "value": v} for d, v in last90]
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
            "x_label": "24 mois" if pts_24m else "90 j",
            "y_unit": "bpm",
            "y_min": (min(p["value"] for p in pts) - 3) if pts else (min(v for _, v in last90) - 3),
            "y_max": (max(p["value"] for p in pts) + 3) if pts else (max(v for _, v in last90) + 3),
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


def build_detail_wegovy(measurements: list[dict], today: date) -> dict:
    """Full titration plan with annotated weight trajectory + STEP-1 methodology."""
    start = date.fromisoformat(WEGOVY_START_ISO)
    day = (today - start).days
    week = day / 7
    wt_end = latest_weight_kg(measurements)
    current_kg = wt_end[0] if wt_end else START_KG
    real_loss = START_KG - current_kg
    predicted_loss = START_KG - weight_ideal(week)

    # Build dose schedule rows: ladder steps annotated with dates.
    schedule_rows: list[dict] = []
    for i, step in enumerate(WEGOVY_LADDER, start=1):
        lo, hi = step["weeks"]
        step_start = start + timedelta(weeks=lo)
        if hi is None:
            label = "à partir de " + fmt_date_fr(step_start)
        else:
            step_end = start + timedelta(weeks=hi)
            label = f"{fmt_date_fr(step_start)} → {fmt_date_fr(step_end - timedelta(days=1))}"
        marker = "→ en cours" if (hi is None or week < hi) and week >= lo else ("✓" if week >= (hi or 999) else "")
        schedule_rows.append({
            "date": f"étape {i}",
            "value": f"{step['dose_mg']} mg",
            "unit": label + (f"  ({marker})" if marker else ""),
        })

    # Real weight trajectory points.
    pts = [
        {"date": fmt_date_fr(start + timedelta(days=int(p["week"] * 7))), "value": p["kg"]}
        for p in real_weight_points(measurements, start, today)
    ]
    # Ideal trajectory along same window for comparison band.
    ideal = []
    for w in range(0, int(week) + 1):
        ideal.append({"date": fmt_date_fr(start + timedelta(weeks=w)), "value": round(weight_ideal(w), 2)})

    return {
        "key": "wegovy",
        "title": "Wegovy · plan de titration",
        "meta": f"J + {day} · dose {WEGOVY_LADDER[min(len(WEGOVY_LADDER) - 1, int(week / 4))]['dose_mg']} mg",
        "hero": {
            "figure": f"−{real_loss:.1f}".replace(".", ","),
            "unit": f"kg en {day} j",
            "delta_label": f"vs −{predicted_loss:.1f} kg STEP-1 prédit".replace(".", ","),
            "status_label": "Sur trajectoire" if abs(real_loss - predicted_loss) < 1.5 else "Plus rapide" if real_loss > predicted_loss else "Plus lent",
            "status_off": False,
        },
        "trajectory": {
            "x_label": f"semaines depuis J1 ({fmt_date_fr(start)})",
            "y_unit": "kg",
            "y_min": 73,
            "y_max": 87,
            "points": pts,
            "ideal": ideal,
            "target": {"value": 75, "label": "objectif 75 kg"},
            "tolerance": 0.8,
        },
        "table": schedule_rows,
        "method": [
            {"heading": "Sémaglutide 2.4 mg", "body": "Wegovy = sémaglutide injectable hebdomadaire (Novo Nordisk). Agoniste GLP-1, supprime l'appétit central et ralentit la vidange gastrique. Titration 5 paliers sur 16 semaines pour limiter les effets digestifs."},
            {"heading": "Modèle de référence STEP-1", "body": "Trajectoire idéale = fit stretched-exponential (forme Weibull, k=1,4) sur la cohorte sémaglutide 2,4 mg du trial STEP-1 (Wilding et al., NEJM 2021). Asymptote 74 kg, time-constant 20 semaines. Le coefficient ~6 500 kcal/kg utilisé pour estimer le déficit énergétique est dans la fourchette 5 400-6 000 publiée par Hall 2011 Lancet (modèle dynamique perte poids global, pas pure adipeuse à 7 700)."},
            {"heading": "Effets indésirables typiques", "body": "Nausées (44 %), diarrhée (32 %), constipation (24 %), vomissements (24 %). Pic au changement de dose, baisse en 2 à 3 semaines. Si persistant: titration ralentie d'1 palier."},
        ],
        "cross_link": {"label": "Voir signal Réponse Wegovy", "href": "/#wegovy_response"},
    }


def build_pillar_detail_recovery(hc_records: list[dict], today: date, huawei_daily: list[dict] | None = None) -> dict | None:
    """Sleep + HRV. Prefers huawei_daily (5 y history with sleep phases),
    falls back to Health Connect."""
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

    # 6 months weekly trajectory + deep/REM sub-trajectories from huawei_daily.
    weekly_pts: list[dict] = []
    deep_pct_pts: list[dict] = []
    rem_pct_pts: list[dict] = []
    if huawei_daily:
        cutoff_6m = today - timedelta(days=183)
        weekly: dict[str, list[float]] = {}
        deep_w: dict[str, list[float]] = {}
        rem_w: dict[str, list[float]] = {}
        for r in huawei_daily:
            if r.get("sleep_total_min") is None or float(r["sleep_total_min"]) <= 0:
                continue
            d = date.fromisoformat(r["date"])
            if d < cutoff_6m:
                continue
            iso_year, iso_week, _ = d.isocalendar()
            key = f"{iso_year:04d}-W{iso_week:02d}"
            total = float(r["sleep_total_min"])
            weekly.setdefault(key, []).append(total)
            if r.get("sleep_deep_min") is not None:
                deep_w.setdefault(key, []).append(float(r["sleep_deep_min"]) / total * 100)
            if r.get("sleep_rem_min") is not None:
                rem_w.setdefault(key, []).append(float(r["sleep_rem_min"]) / total * 100)
        for key in sorted(weekly):
            label = key.split("-W")[1] + "/" + key.split("-")[0][2:]
            weekly_pts.append({"date": "S" + label, "value": int(round(sum(weekly[key]) / len(weekly[key])))})
            if key in deep_w:
                deep_pct_pts.append({"date": "S" + label, "value": round(sum(deep_w[key]) / len(deep_w[key]), 1)})
            if key in rem_w:
                rem_pct_pts.append({"date": "S" + label, "value": round(sum(rem_w[key]) / len(rem_w[key]), 1)})
    return {
        "key": "recovery",
        "title": "Récupération",
        "meta": "Huawei Watch GT2 · sommeil via Health Connect" + (" + huawei_daily (5 ans)" if weekly_pts else ""),
        "hero": {
            "figure": f"{h} h {m:02d}",
            "unit": "moyenne 7 j",
            "status_label": "Conforme" if avg_min >= 420 else "Dérive mineure" if avg_min >= 360 else "Dérive notable",
            "status_off": avg_min < 420,
        },
        "trajectory": {
            "x_label": "6 mois (sem.)" if weekly_pts else "30 j",
            "y_unit": "min",
            "y_min": 240,
            "y_max": 540,
            "points": weekly_pts if weekly_pts else pts,
            "target": {"value": 420, "label": "cible 7 h"},
            "tolerance": 30,
        },
        "sub_trajectories": [
            {"key": "deep_pct", "label": "Sommeil profond (%)", "y_unit": "%", "points": deep_pct_pts, "target": {"value": 15, "label": "cible 13-23 %"}},
            {"key": "rem_pct", "label": "Sommeil REM (%)", "y_unit": "%", "points": rem_pct_pts, "target": {"value": 22, "label": "cible 20-25 %"}},
        ] if (deep_pct_pts or rem_pct_pts) else [],
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

CATEGORY_LABELS = {
    "lipids": "Lipides",
    "cardio_risk": "Risque cardio",
    "cardio_enzyme": "Enzymes cardiaques",
    "inflammation": "Inflammation",
    "metabolic": "Métabolique",
    "renal": "Rénal",
    "hepatic": "Hépatique",
    "cbc": "Hématologie (NFS)",
    "iron": "Fer",
    "vitamin": "Vitamines",
    "endocrine": "Hormones",
    "thyroid": "Thyroïde",
    "electrolyte": "Électrolytes",
    "micronutrient": "Micronutriments",
    "autoimmune": "Auto-immun",
    "tumor": "Marqueurs tumoraux",
    "infection": "Infection",
}
CATEGORY_ORDER = [
    "lipids", "cardio_risk", "cardio_enzyme", "inflammation",
    "metabolic", "renal", "hepatic", "cbc", "endocrine", "thyroid",
    "vitamin", "iron", "micronutrient", "electrolyte",
    "autoimmune", "tumor", "infection",
]


def build_biology(panels: list[dict], results: list[dict], today: date,
                  measurements: list[dict] | None = None,
                  chrono_yr: float = 35.2, bmi: float = 28.8) -> dict | None:
    """Top-level biology card: PhenoAge + Lifetime CV + PREVENT 30y + cycle."""
    if not panels:
        return None
    latest_pair = latest_lab_panel(panels, results)
    if not latest_pair:
        return None
    latest_panel, markers = latest_pair
    pa = phenoage_levine(markers, chrono_yr)
    if pa is None:
        return None
    sbp, dbp = avg_bp_28d(measurements or [], today)
    risk_pct, risk_label, risk_driver = lifetime_cv_risk_full(markers, chrono_yr, bmi, sbp, dbp)
    prevent_pct = prevent_30y_total_cvd(markers, sbp, dbp, chrono_yr, bmi)
    prevent_band_key, prevent_band_label = prevent_band(prevent_pct)
    coll = datetime.fromisoformat(latest_panel["collected_at"].replace("Z", "+00:00")).date()
    days_since = (today - coll).days
    next_recommended = coll + timedelta(days=180)
    days_until_next = (next_recommended - today).days
    return {
        "last_panel_date": coll.isoformat(),
        "last_panel_label": fmt_date_fr(coll),
        "lab_name": latest_panel.get("lab_name") or "labo",
        "phenoage": round(pa, 1),
        "phenoage_delta": round(pa - chrono_yr, 1),
        "lifetime_cv_risk_pct": risk_pct,
        "lifetime_cv_risk_label": risk_label,
        "lifetime_cv_risk_driver": risk_driver,
        "prevent_30y_pct": prevent_pct,
        "prevent_30y_band": prevent_band_key,
        "prevent_30y_band_label": prevent_band_label,
        "sbp_avg": round(sbp) if sbp else None,
        "dbp_avg": round(dbp) if dbp else None,
        "days_since_last": days_since,
        "days_until_next": days_until_next,
        "next_recommended_date": next_recommended.isoformat(),
        "n_markers": len(markers),
    }


def build_detail_biology(panels: list[dict], results: list[dict], today: date, chrono_yr: float = 35.2) -> dict | None:
    """Full lab report page: every marker grouped by system, deltas vs baseline."""
    if not panels:
        return None
    pair = latest_lab_panel(panels, results)
    if not pair:
        return None
    latest_panel, latest_markers = pair
    # Find prior panel (baseline) if any.
    prior_panels = [p for p in panels if p["id"] != latest_panel["id"]]
    prior_markers: dict = {}
    prior_panel = None
    if prior_panels:
        prior_panel = max(prior_panels, key=lambda p: p["collected_at"])
        prior_markers = {r["marker_code"]: r for r in results if r["panel_id"] == prior_panel["id"]}

    # Group markers by category, ordered per CATEGORY_ORDER.
    grouped: dict[str, list[dict]] = {}
    for code, r in latest_markers.items():
        cat = r.get("category") or "other"
        grouped.setdefault(cat, []).append(r)

    sections = []
    for cat in CATEGORY_ORDER:
        rows = grouped.get(cat)
        if not rows:
            continue
        # Sort by abnormal first, then by label.
        rows_sorted = sorted(rows, key=lambda r: (r.get("flag") is None, r.get("marker_label") or ""))
        section_markers = []
        for r in rows_sorted:
            prior = prior_markers.get(r["marker_code"])
            delta_str = None
            delta_pct = None
            baseline_num = None
            v_num = float(r["value_num"]) if r.get("value_num") is not None else None
            if prior and v_num is not None and prior.get("value_num") is not None:
                p = float(prior["value_num"])
                baseline_num = p
                if p != 0:
                    delta_str = f"{'+' if v_num >= p else '−'}{abs(v_num - p):.2f}".replace(".", ",").rstrip("0").rstrip(",")
                    delta_pct = round((v_num - p) / p * 100, 1)
            value_display = (
                f"{v_num:g}".replace(".", ",")
                if v_num is not None
                else (r.get("value_text") or "—")
            )
            ref_low_num = float(r["ref_low"]) if r.get("ref_low") is not None else None
            ref_high_num = float(r["ref_high"]) if r.get("ref_high") is not None else None
            section_markers.append({
                "code": r["marker_code"],
                "label": r.get("marker_label") or r["marker_code"],
                "value": value_display,
                "value_num": v_num,
                "baseline_num": baseline_num,
                "unit": r.get("unit") or "",
                "ref_low": ref_low_num,
                "ref_high": ref_high_num,
                "flag": r.get("flag"),
                "delta_str": delta_str,
                "delta_pct": delta_pct,
            })
        sections.append({
            "key": cat,
            "label": CATEGORY_LABELS.get(cat, cat.title()),
            "markers": section_markers,
        })

    coll = datetime.fromisoformat(latest_panel["collected_at"].replace("Z", "+00:00")).date()
    prior_coll = (
        datetime.fromisoformat(prior_panel["collected_at"].replace("Z", "+00:00")).date()
        if prior_panel else None
    )
    pa = phenoage_levine(latest_markers, chrono_yr)
    risk_pct, risk_label = lifetime_cv_risk(latest_markers, chrono_yr)
    return {
        "key": "biology",
        "title": "Bilan biologique",
        "meta": f"{latest_panel.get('lab_name','labo')} · prélevé {fmt_date_fr(coll)} · {len(latest_markers)} marqueurs"
                + (f" · baseline {fmt_date_fr(prior_coll)}" if prior_coll else ""),
        "hero": {
            "figure": f"{pa:.0f}",
            "unit": f"ans · PhenoAge ({pa - chrono_yr:+.1f} vs chrono)".replace(".", ","),
            "delta_label": f"Risque CV à vie {risk_pct} % · {risk_label}",
            "status_label": "Bilan à jour" if (today - coll).days < 180 else "À renouveler",
            "status_off": (today - coll).days >= 200,
        },
        "trajectory": {
            "x_label": "PhenoAge mesurée",
            "y_unit": "ans",
            "y_min": max(20, int(pa) - 5),
            "y_max": int(chrono_yr) + 10,
            "points": ([{"date": fmt_date_fr(prior_coll), "value": chrono_yr}] if prior_coll else [])
                + [{"date": fmt_date_fr(coll), "value": round(pa, 1)}],
            "target": {"value": chrono_yr, "label": f"chrono {int(chrono_yr)} ans"},
        },
        "table": [],
        "subs": [],
        "method": [
            {"heading": "PhenoAge (Levine 2018)", "body": "Levine ML et al., Aging (Albany NY) 2018;10(4):573-91. Validée sur NHANES III (n=9 926) + UK Biobank, prédit mortalité 10 ans avec C-statistic 0,84. Combine 9 marqueurs : albumine, créatinine, glucose, hsCRP, lymphocytes %, MCV, RDW, ALP, leucocytes + âge chronologique. NE prend PAS en compte cholestérol/LDL (qui mesurent risque CV futur, pas vieillissement actuel)."},
            {"heading": "Risque CV à vie (Berry NEJM 2012)", "body": "Lifetime Risk validé sur cohortes CARDIA + Framingham + ARIC + MESA, modèle Lloyd-Jones 2006. 5 catégories (tous optimaux 1,4 % · sous-optimal 5,6 % · élevé 35,6 % · majeur 45,5 % · 2+ majeurs 68,9 %, depuis index age 45). Seuils TA = ACC/AHA 2002 (cadrage de l'étude). Levier majeur : chaque −1 mmol/L LDL = −22 % d'événements CV (CTT Collaboration Lancet 2010)."},
            {"heading": "Score CV 30 ans (heuristique)", "body": "Approximation multiplicative ad-hoc, PAS l'équation officielle PREVENT 2023. Anchor : 7,5 % pour profil optimal 35M, multiplicateurs dérivés des relative risks publiés. À remplacer par les coefficients Cox exacts du eAppendix Khan Circulation 2024;149:430-449 pour usage clinique."},
            {"heading": "Cycle de renouvellement", "body": "Recommandation bilan complet tous les 6 mois en phase Wegovy active. Cycle de référence : 180 jours."},
        ],
        "sections": sections,
    }


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
    huawei_daily = sb_get("huawei_daily", {"select": "*", "order": "date.desc"})
    panels = sb_get("lab_panel", {"select": "id,collected_at,lab_name,panel_name", "order": "collected_at.desc"})
    results = sb_get("lab_result", {"select": "panel_id,marker_code,marker_label,value_num,value_text,unit,ref_low,ref_high,flag,category"})
    labs = latest_lab_panel(panels, results)
    print(f"  loaded: {len(measurements)} withings rows, {len(activity)} activity days, {len(yazio)} yazio days, {len(hc_records)} HC records, {len(huawei_daily)} huawei_daily rows, {len(panels)} lab panels ({len(results)} results)", file=sys.stderr)

    pillar_detail: dict[str, Any] = {}
    comp_detail = build_pillar_detail_composition(measurements, today)
    if comp_detail:
        pillar_detail["composition"] = comp_detail
    act_detail = build_pillar_detail_activity(activity, today)
    if act_detail:
        pillar_detail["activity"] = act_detail
    cardio_detail = build_pillar_detail_cardio(activity, today, huawei_daily)
    if cardio_detail:
        pillar_detail["cardio"] = cardio_detail
    recovery_detail = build_pillar_detail_recovery(hc_records, today, huawei_daily)
    if recovery_detail:
        pillar_detail["recovery"] = recovery_detail
    pillar_detail["wegovy"] = build_detail_wegovy(measurements, today)
    bio_detail = build_detail_biology(panels, results, today)
    if bio_detail:
        pillar_detail["biology"] = bio_detail

    sigs = build_signals(yazio, measurements, activity, hc_records, today, huawei_daily)
    payload: dict[str, Any] = {
        "today": today.isoformat(),
        "hero": build_hero(measurements, today),
        "wegovy": build_wegovy(today),
        "signals": sigs,
        "action_today": build_action_today(sigs),
        "bio_age": build_bio_age(measurements, activity, labs, hc_records, huawei_daily, today),
        "biology": build_biology(panels, results, today, measurements),
        "pillars": build_pillars(yazio, measurements, activity, hc_records, today),
        "pillar_detail": pillar_detail,
    }
    # AI brief: 1-sentence analysis from Claude Haiku given full context.
    raw_context = {
        "n_weight_measurements": sum(1 for m in measurements if m["type_code"] == 1),
        "n_bp_measurements": sum(1 for m in measurements if m["type_code"] in (9, 10)),
        "n_yazio_days": len(yazio),
        "n_hc_records": len(hc_records),
        "latest_labs": [
            {"code": r["marker_code"], "value": r.get("value_num"), "unit": r.get("unit"), "flag": r.get("flag")}
            for r in results
            if labs and r["panel_id"] == labs[0]["id"]
        ] if labs else [],
        "lab_panel_date": labs[0]["collected_at"] if labs else None,
        "huawei_5y_summary": _huawei_5y_summary(huawei_daily, today) if huawei_daily else None,
    }
    payload["ai_brief"] = build_ai_brief(payload, raw_context)

    sb_upsert(
        [{"snapshot_date": today.isoformat(), "payload": payload}],
        "cockpit_snapshot",
        "snapshot_date",
    )
    print(f"done. hero={payload['hero']['current_kg']}kg ({payload['hero']['statusLabel']}), signals={len(payload['signals'])}, pillars={len(payload['pillars'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
