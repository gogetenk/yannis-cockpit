"""Aggregate a decrypted Huawei Health export into ONE row per day.

Walks "Health detail data & description/*.json", buckets every samplePoint
by local date, and computes daily summaries (rest HR avg/min, sleep phase
durations, stress avg/max, etc.). Pushes the rolled-up rows to the
`huawei_daily` Supabase table. ~2k rows total for a 5-year export.

Idempotent: re-running upserts the same dates.

Usage:
    SUPABASE_URL=...  SUPABASE_SERVICE_ROLE_KEY=... \\
    python ingest/huawei/ingest_huawei_export.py /path/to/extracted/export
"""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

import requests


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"missing env: {name}")
    return v


def headers() -> dict:
    key = env("SUPABASE_SERVICE_ROLE_KEY")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def parse_value(raw_str: str) -> tuple[float | None, dict | None]:
    if not raw_str:
        return None, None
    try:
        d = json.loads(raw_str) if raw_str.startswith("{") or raw_str.startswith("[") else None
    except json.JSONDecodeError:
        d = None
    if isinstance(d, dict):
        for v in d.values():
            if isinstance(v, (int, float)):
                return float(v), d
        return None, d
    try:
        return float(raw_str), None
    except (ValueError, TypeError):
        return None, None


def upload(rows: list[dict]) -> None:
    if not rows:
        return
    url = f"{env('SUPABASE_URL')}/rest/v1/huawei_daily?on_conflict=date"
    r = requests.post(url, headers=headers(), data=json.dumps(rows), timeout=60)
    if not r.ok:
        sys.exit(f"upload failed {r.status_code}: {r.text[:400]}")


# Per-day accumulator
def make_day() -> dict:
    return {
        "rest_hr_vals": [],
        "hr_vals": [],
        "sleep_min": defaultdict(int),  # phase → minutes
        "stress_vals": [],
        "exercise_intensity": 0,
        "active_hours": 0,
        "spo2_vals": [],
        "basal_kcal": 0,
        "weight": None,
        "body_fat": None,
        "n": 0,
    }


SLEEP_PHASE_KEYS = {
    "PROFESSIONAL_SLEEP_SHALLOW": "light",
    "PROFESSIONAL_SLEEP_DEEP": "deep",
    "PROFESSIONAL_SLEEP_DREAM": "rem",
    "PROFESSIONAL_SLEEP_WAKE": "wake",
    "PROFESSIONAL_SLEEP_NOON": "noon",
}


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: ingest_huawei_export.py /path/to/extracted/export")
    root = sys.argv[1].rstrip("/\\")
    files = sorted(glob.glob(os.path.join(root, "Health detail data & description", "*.json")))
    if not files:
        sys.exit(f"no JSON files under {root}")
    print(f"→ {len(files)} files to scan", file=sys.stderr)

    by_day: dict[date, dict] = defaultdict(make_day)
    # Dedup sleep phases across all files/sources: watch + phone + sync
    # may emit the same physical sleep session multiple times.
    seen_sleep_sessions: set = set()

    for i, f in enumerate(files, 1):
        try:
            data = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            print(f"  skip {os.path.basename(f)}: {e}", file=sys.stderr)
            continue
        for rec in data:
            for sp in rec.get("samplePoints", []):
                start = sp.get("startTime")
                end = sp.get("endTime")
                key = sp.get("key")
                if not start or not key:
                    continue
                d = datetime.fromtimestamp(start / 1000, tz=timezone.utc).date()
                bucket = by_day[d]
                bucket["n"] += 1
                val_num, raw = parse_value(sp.get("value", ""))

                if key in ("RESTING_HEART_RATE", "DATA_POINT_NEW_REST_HEARTRATE", "DATA_POINT_REST_HEARTRATE"):
                    if val_num and 30 < val_num < 120:
                        bucket["rest_hr_vals"].append(val_num)
                elif key in ("DYNAMIC_HEART_RATE", "DATA_POINT_DYNAMIC_HEARTRATE"):
                    if val_num and 30 < val_num < 220:
                        bucket["hr_vals"].append(val_num)
                elif key in SLEEP_PHASE_KEYS and end:
                    sess_key = (start, end, key)
                    if sess_key in seen_sleep_sessions:
                        continue
                    seen_sleep_sessions.add(sess_key)
                    minutes = round((end - start) / 60000)
                    if 0 < minutes < 24 * 60:
                        bucket["sleep_min"][SLEEP_PHASE_KEYS[key]] += minutes
                elif key in ("STRESS", "STRESS_DATA"):
                    if val_num and 0 <= val_num <= 100:
                        bucket["stress_vals"].append(val_num)
                elif key == "EXERCISE_INTENSITY":
                    if val_num and val_num > 0:
                        bucket["exercise_intensity"] += int(val_num)
                elif key == "ACTIVE_HOUR":
                    bucket["active_hours"] += 1
                elif key == "BLOOD_OXYGEN_SATURATION":
                    if val_num and 70 <= val_num <= 100:
                        bucket["spo2_vals"].append(val_num)
                elif key == "BASAL_METABOLISM":
                    if val_num and val_num > 0:
                        bucket["basal_kcal"] += int(val_num)
                elif key == "WEIGHT_BODYFAT_BROAD" and isinstance(raw, dict):
                    if "weight" in raw:
                        bucket["weight"] = float(raw["weight"])
                    if "bodyFat" in raw:
                        bucket["body_fat"] = float(raw["bodyFat"])

        if i % 25 == 0:
            print(f"  [{i}/{len(files)}]", file=sys.stderr)

    print(f"  aggregating to {len(by_day)} days", file=sys.stderr)

    rows: list[dict] = []
    for d, b in sorted(by_day.items()):
        sleep_phases = b["sleep_min"]
        total_sleep = sum(v for k, v in sleep_phases.items() if k != "noon")
        # Sanity cap: post-dedup, > 12h of nightly sleep is implausible.
        if total_sleep > 720:
            total_sleep = 0
            sleep_phases = defaultdict(int)
        rows.append({
            "date": d.isoformat(),
            "rest_hr_avg": round(sum(b["rest_hr_vals"]) / len(b["rest_hr_vals"]), 1) if b["rest_hr_vals"] else None,
            "rest_hr_min": min(b["rest_hr_vals"]) if b["rest_hr_vals"] else None,
            "hr_continuous_avg": round(sum(b["hr_vals"]) / len(b["hr_vals"]), 1) if b["hr_vals"] else None,
            "hr_continuous_max": max(b["hr_vals"]) if b["hr_vals"] else None,
            "sleep_total_min": total_sleep if total_sleep else None,
            "sleep_deep_min": sleep_phases.get("deep") or None,
            "sleep_light_min": sleep_phases.get("light") or None,
            "sleep_rem_min": sleep_phases.get("rem") or None,
            "sleep_wake_min": sleep_phases.get("wake") or None,
            "sleep_noon_min": sleep_phases.get("noon") or None,
            "stress_avg": round(sum(b["stress_vals"]) / len(b["stress_vals"]), 1) if b["stress_vals"] else None,
            "stress_max": max(b["stress_vals"]) if b["stress_vals"] else None,
            "exercise_intensity_sum": b["exercise_intensity"] or None,
            "active_hours": b["active_hours"] or None,
            "spo2_avg": round(sum(b["spo2_vals"]) / len(b["spo2_vals"]), 1) if b["spo2_vals"] else None,
            "basal_metabolism_kcal": b["basal_kcal"] or None,
            "weight_kg": b["weight"],
            "body_fat_pct": b["body_fat"],
            "n_samples": b["n"],
        })

    # Push in 500-row chunks
    pushed = 0
    for i in range(0, len(rows), 500):
        upload(rows[i:i + 500])
        pushed += len(rows[i:i + 500])
    print(f"done. {pushed} day rows pushed (range {rows[0]['date']} → {rows[-1]['date']})",
          file=sys.stderr)


if __name__ == "__main__":
    main()
