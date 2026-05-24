"""Run the Huawei aggregation logic but dump to a JSON file instead of
pushing to Supabase. Used so I can then push via MCP without needing
the service role key locally."""
from __future__ import annotations
import glob, json, os, sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from ingest_huawei_export import (
    parse_value, make_day, SLEEP_PHASE_KEYS,
)

def run(root: str, out_path: str) -> None:
    files = sorted(glob.glob(os.path.join(root, "Health detail data & description", "*.json")))
    by_day: dict = defaultdict(make_day)
    # Dedup sleep phases across all files/sources: watch + phone + sync
    # may emit the same physical sleep session multiple times.
    seen_sleep_sessions: set = set()
    for f in files:
        try:
            data = json.load(open(f, encoding="utf-8"))
        except Exception:
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

    rows = []
    for d, b in sorted(by_day.items()):
        sleep_phases = b["sleep_min"]
        total_sleep = sum(v for k, v in sleep_phases.items() if k != "noon")
        # Sanity cap: post-dedup, > 12h of nightly sleep is implausible.
        # Most likely residual overlap we couldn't dedup → drop the row.
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
            "n_samples": b["n"],
        })
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(rows, fp)
    print(f"wrote {len(rows)} rows to {out_path}", file=sys.stderr)

if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
