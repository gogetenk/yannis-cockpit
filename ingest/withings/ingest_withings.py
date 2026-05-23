"""
Withings → Supabase ingest.

- Pulls the OAuth token pair from `withings_oauth`, refreshes the access
  token if expired (3h TTL), stores the rotated pair back.
- Calls `measure/getmeas` with `lastupdate=` for incremental syncs (default),
  or `startdate`/`enddate` for backfills (set BACKFILL_DAYS env).
- Flattens every measure point into `withings_measurement` rows. Idempotent
  on (ts, type_code, measure_grp_id).

Env vars:
    WITHINGS_CLIENT_ID
    WITHINGS_CLIENT_SECRET
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
    BACKFILL_DAYS              Optional. If set, ignore lastupdate and pull
                               the trailing N days. Use once at bootstrap.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

import requests


TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL = "https://wbsapi.withings.net/measure"
MEASURE_V2_URL = "https://wbsapi.withings.net/v2/measure"
DEFAULT_LASTUPDATE_WINDOW_DAYS = 14  # safety net if state lost

# Withings measure types we care about (extend as needed). Labels are for
# human inspection in the DB; the type_code is the canonical id.
TYPE_LABELS: dict[int, tuple[str, str]] = {
    1: ("weight", "kg"),
    4: ("height", "m"),
    5: ("fat_free_mass", "kg"),
    6: ("fat_ratio", "%"),
    8: ("fat_mass", "kg"),
    9: ("diastolic_bp", "mmHg"),
    10: ("systolic_bp", "mmHg"),
    11: ("heart_pulse", "bpm"),
    12: ("temperature", "C"),
    54: ("spo2", "%"),
    71: ("body_temperature", "C"),
    73: ("skin_temperature", "C"),
    76: ("muscle_mass", "kg"),
    77: ("hydration", "kg"),
    88: ("bone_mass", "kg"),
    91: ("pulse_wave_velocity", "m/s"),
    123: ("vo2_max", "ml/kg/min"),
    130: ("afib_ecg", ""),
    135: ("qrs_interval", "ms"),
    136: ("pr_interval", "ms"),
    137: ("qt_interval", "ms"),
    138: ("corrected_qt", "ms"),
    139: ("afib_ppg", ""),
    155: ("vascular_age", "year"),
    167: ("nerve_health_score", "uS"),
    # Empirical confirmation (Body Scan, 38 measurements):
    # 168 (~19kg) + 169 (~29kg) = 77 hydration total (~48kg). Per ICW > ECW
    # convention, 168 = extracellular, 169 = intracellular.
    # 170 (~3) is a 0-12 score, matches visceral fat.
    168: ("extracellular_water", "kg"),
    169: ("intracellular_water", "kg"),
    170: ("visceral_fat_score", ""),
    174: ("fat_mass_segment", "kg"),
    175: ("muscle_mass_segment", "kg"),
    196: ("electrodermal_activity_feet", "uS"),
    226: ("basal_metabolic_rate", "kcal"),
}
ALL_TYPES = ",".join(str(t) for t in TYPE_LABELS)


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
    r = requests.get(
        f"{env('SUPABASE_URL')}/rest/v1/{path}",
        headers=sb_headers(),
        params=params,
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def sb_upsert(rows: list[dict], table: str, on_conflict: str) -> None:
    if not rows:
        return
    # Postgres ON CONFLICT rejects batches that touch the same PK twice
    # in a single statement. Withings occasionally returns duplicate
    # measures within a group (e.g. two weight readings same second); we
    # keep the last one seen for each PK before posting.
    keys = on_conflict.split(",")
    deduped: dict[tuple, dict] = {}
    for r in rows:
        deduped[tuple(r[k] for k in keys)] = r
    payload = list(deduped.values())
    url = f"{env('SUPABASE_URL')}/rest/v1/{table}?on_conflict={on_conflict}"
    headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
    if not resp.ok:
        sys.exit(f"upsert {table} failed {resp.status_code}: {resp.text[:500]}")


def load_token() -> dict:
    rows = sb_get("withings_oauth", {"id": "eq.1", "select": "*"})
    if not rows:
        sys.exit("withings_oauth row missing. Run setup_oauth.py first.")
    return rows[0]


def refresh_if_needed(tok: dict) -> dict:
    expires_at = datetime.fromisoformat(tok["expires_at"])
    if expires_at - datetime.now(timezone.utc) > timedelta(minutes=10):
        return tok  # still fresh
    print("→ refreshing access token", file=sys.stderr)
    payload = {
        "action": "requesttoken",
        "client_id": env("WITHINGS_CLIENT_ID"),
        "client_secret": env("WITHINGS_CLIENT_SECRET"),
        "grant_type": "refresh_token",
        "refresh_token": tok["refresh_token"],
    }
    r = requests.post(TOKEN_URL, data=payload, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != 0:
        sys.exit(f"refresh failed: {json.dumps(data)}")
    body = data["body"]
    new = {
        "id": 1,
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=int(body["expires_in"]))).isoformat(),
        "userid": str(body.get("userid", tok.get("userid", ""))),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    sb_upsert([new], "withings_oauth", "id")
    return new


def fetch_measures(access_token: str, params: dict) -> Iterable[dict]:
    """Iterate measure groups across pagination."""
    offset = 0
    while True:
        q = {"action": "getmeas", "meastypes": ALL_TYPES, **params}
        if offset:
            q["offset"] = offset
        r = requests.post(
            MEASURE_URL,
            data=q,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") != 0:
            sys.exit(f"getmeas failed: {json.dumps(data)[:500]}")
        body = data["body"]
        for grp in body.get("measuregrps", []):
            yield grp
        if not body.get("more"):
            return
        offset = body.get("offset", 0)


def rows_from_group(grp: dict) -> list[dict]:
    """Flatten one measuregrp into per-measure rows. Body Scan returns the
    same type_code with different `position` values (e.g. 7=global, 2=trunk,
    1/3/4/5=limbs), so position is part of the natural key."""
    ts_iso = datetime.fromtimestamp(grp["date"], tz=timezone.utc).isoformat()
    out = []
    for m in grp.get("measures", []):
        type_code = m["type"]
        value = m["value"] * (10 ** m["unit"])
        label, unit = TYPE_LABELS.get(type_code, (f"type_{type_code}", ""))
        out.append({
            "ts": ts_iso,
            "type_code": type_code,
            "position": m.get("position", 0),
            "value": value,
            "unit": unit,
            "device_id": grp.get("deviceid"),
            "measure_grp_id": grp.get("grpid"),
            "type_label": label,
            "raw": m,
        })
    return out


def fetch_activities(access_token: str, start_ymd: str, end_ymd: str) -> Iterable[dict]:
    """Iterate daily activity rows (steps, distance, intensity minutes, kcal).
    Source can be a Withings tracker OR an external app (Health Connect,
    Strava…) since Withings aggregates from connected sources."""
    offset = 0
    while True:
        q = {
            "action": "getactivity",
            "startdateymd": start_ymd,
            "enddateymd": end_ymd,
            "data_fields": "steps,distance,elevation,soft,moderate,intense,active,calories,totalcalories,hr_average,hr_min,hr_max,hr_zone_0,hr_zone_1,hr_zone_2,hr_zone_3",
        }
        if offset:
            q["offset"] = offset
        r = requests.post(
            MEASURE_V2_URL,
            data=q,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") != 0:
            sys.exit(f"getactivity failed: {json.dumps(data)[:500]}")
        body = data["body"]
        for act in body.get("activities", []):
            yield act
        if not body.get("more"):
            return
        offset = body.get("offset", 0)


def row_from_activity(act: dict) -> dict:
    """Project one activity day into a yazio-style row."""
    return {
        "date": act["date"],
        "steps": act.get("steps"),
        "distance_m": act.get("distance"),
        "elevation_m": act.get("elevation"),
        "soft_min": act.get("soft"),
        "moderate_min": act.get("moderate"),
        "intense_min": act.get("intense"),
        "active_min": act.get("active"),
        "active_kcal": act.get("calories"),
        "total_kcal": act.get("totalcalories"),
        "timezone": act.get("timezone"),
        "source_model": act.get("model"),
        "raw": act,
    }


def latest_ts() -> int | None:
    """Highest ts we already have, as epoch seconds."""
    rows = sb_get("withings_measurement", {
        "select": "ts",
        "order": "ts.desc",
        "limit": "1",
    })
    if not rows:
        return None
    ts = datetime.fromisoformat(rows[0]["ts"].replace("Z", "+00:00"))
    return int(ts.timestamp())


def main() -> None:
    for k in ("WITHINGS_CLIENT_ID", "WITHINGS_CLIENT_SECRET", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        env(k)
    tok = refresh_if_needed(load_token())

    backfill = os.environ.get("BACKFILL_DAYS")
    if backfill:
        start = int((datetime.now(timezone.utc) - timedelta(days=int(backfill))).timestamp())
        end = int(time.time())
        params = {"startdate": start, "enddate": end}
        print(f"→ backfill {backfill} days ({start} → {end})", file=sys.stderr)
    else:
        lu = latest_ts()
        if lu is None:
            lu = int((datetime.now(timezone.utc) - timedelta(days=DEFAULT_LASTUPDATE_WINDOW_DAYS)).timestamp())
        params = {"lastupdate": lu}
        print(f"→ incremental lastupdate={lu} ({datetime.fromtimestamp(lu, tz=timezone.utc).isoformat()})", file=sys.stderr)

    all_rows: list[dict] = []
    grp_count = 0
    for grp in fetch_measures(tok["access_token"], params):
        all_rows.extend(rows_from_group(grp))
        grp_count += 1
        # Flush in batches so we don't hold everything in RAM on a big backfill.
        if len(all_rows) >= 500:
            sb_upsert(all_rows, "withings_measurement", "ts,type_code,measure_grp_id,position")
            print(f"  flushed {len(all_rows)} rows ({grp_count} groups)", file=sys.stderr)
            all_rows = []
    if all_rows:
        sb_upsert(all_rows, "withings_measurement", "ts,type_code,measure_grp_id,position")
    print(f"  measurements done. {grp_count} measure groups.", file=sys.stderr)

    # --- Activity (steps, distance, intensity, kcal) -----------------
    if backfill:
        start_dt = datetime.now(timezone.utc) - timedelta(days=int(backfill))
        end_dt = datetime.now(timezone.utc)
    else:
        # Refresh a 14-day tail every run so late syncs from Health Connect
        # (Huawei → Health Sync → HC → Withings can lag a day or two) land
        # without needing webhooks.
        start_dt = datetime.now(timezone.utc) - timedelta(days=14)
        end_dt = datetime.now(timezone.utc)
    start_ymd, end_ymd = start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")
    print(f"→ activity {start_ymd} → {end_ymd}", file=sys.stderr)
    act_rows = [row_from_activity(a) for a in fetch_activities(tok["access_token"], start_ymd, end_ymd)]
    sb_upsert(act_rows, "withings_activity_daily", "date")
    print(f"  activity done. {len(act_rows)} days.", file=sys.stderr)


if __name__ == "__main__":
    main()
