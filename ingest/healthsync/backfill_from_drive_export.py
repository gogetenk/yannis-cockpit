"""One-shot backfill of Huawei → Health Sync → Google Drive CSV exports
into Supabase `hc_raw_record`, bypassing the broken Health Connect bridge.

Reads Health Sync's standard CSV dumps from a folder tree (typically
unzipped from Drive):
  Health Sync Activités/      *.fit / *.tcx / *.csv     workouts
  Health Sync Fréquence cardiaque/  HR mensuel + hebdo CSVs
  Health Sync Sommeil/        daily + weekly sleep with stages

Currently handles:
  - HR monthly CSVs (Date,Heure,bpm,Origine)
  - Sleep daily/weekly CSVs (Date,Heure,Durée_sec,Phase) → sessions reconstructed
    by gluing consecutive stage rows separated by ≤ 30 min

Skipped (out of scope for the current gap-fill, can be added later):
  - .fit / .tcx / .gpx workout files (binary, requires fitparse/python-tcxparser)
  - Activity CSVs (WALKING/RUNNING) — same workouts as above in flat form

Idempotency: record_uid is deterministic = sha1(record_type + start_ts).
Re-runs upsert in place, no duplicates.

Usage:
  python -m ingest.healthsync.backfill_from_drive_export \
      --root C:/repos/yazio-exporter/output \
      --since 2026-05-24 --until 2026-05-31
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

PARIS_TZ_OFFSET = timedelta(hours=2)  # Europe/Paris CEST; CSV times are local

def env(name: str) -> str:
    v = os.environ.get(name)
    if not v: sys.exit(f"missing env var: {name}")
    return v

def to_utc_iso(local_dt: datetime) -> str:
    # CSVs use Europe/Paris local time. Subtract offset for UTC ISO.
    return (local_dt - PARIS_TZ_OFFSET).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

def parse_hr_csv(path: Path, since: date, until: date) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if len(row) < 3: continue
            try:
                dt = datetime.strptime(row[0].strip(), "%Y.%m.%d %H:%M:%S")
                bpm = float(row[2])
            except (ValueError, IndexError):
                continue
            if not (since <= dt.date() <= until): continue
            if bpm <= 0 or bpm > 240: continue
            ts_iso = to_utc_iso(dt)
            uid = "healthsync-hr:" + hashlib.sha1(ts_iso.encode()).hexdigest()[:16]
            rows.append({
                "record_type": "heart_rate",
                "record_uid": uid,
                "start_ts": ts_iso,
                "end_ts": None,
                "value_num": bpm,
                "unit": "bpm",
                "source_app": "healthsync.drive.csv",
                "source_device": "Huawei (via Health Sync)",
                "payload": None,
            })
    return rows

def parse_sleep_csv(path: Path, since: date, until: date) -> list[dict]:
    """Stage rows → sessions: glue consecutive stages with <30 min gap."""
    stages: list[tuple[datetime, int, str]] = []  # (start, duration_sec, stage)
    with path.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if len(row) < 4: continue
            try:
                dt = datetime.strptime(row[0].strip(), "%Y.%m.%d %H:%M:%S")
                dur = int(row[2])
                stage = row[3].strip().lower()
            except (ValueError, IndexError):
                continue
            stages.append((dt, dur, stage))
    if not stages: return []
    stages.sort(key=lambda x: x[0])
    sessions: list[list[tuple[datetime, int, str]]] = [[stages[0]]]
    for s in stages[1:]:
        prev_end = sessions[-1][-1][0] + timedelta(seconds=sessions[-1][-1][1])
        if (s[0] - prev_end) <= timedelta(minutes=30):
            sessions[-1].append(s)
        else:
            sessions.append([s])
    out: list[dict] = []
    for sess in sessions:
        start = sess[0][0]
        end = sess[-1][0] + timedelta(seconds=sess[-1][1])
        wake_date = end.date()
        if not (since <= wake_date <= until): continue
        total_min = sum(s[1] for s in sess) / 60.0
        if total_min < 30: continue  # skip naps under 30 min
        stage_minutes: dict[str, float] = {}
        for s in sess:
            stage_minutes[s[2]] = stage_minutes.get(s[2], 0) + s[1] / 60.0
        start_iso = to_utc_iso(start)
        end_iso = to_utc_iso(end)
        uid = "healthsync-sleep:" + hashlib.sha1(start_iso.encode()).hexdigest()[:16]
        out.append({
            "record_type": "sleep_session",
            "record_uid": uid,
            "start_ts": start_iso,
            "end_ts": end_iso,
            "value_num": total_min,
            "unit": "min",
            "source_app": "healthsync.drive.csv",
            "source_device": "Huawei (via Health Sync)",
            "payload": {
                "stages": [
                    {"stage": s[2], "start": to_utc_iso(s[0]),
                     "minutes": round(s[1] / 60.0, 1)}
                    for s in sess
                ],
                "stage_minutes": {k: round(v, 1) for k, v in stage_minutes.items()},
            },
        })
    return out


def sb_upsert(rows: list[dict], batch: int = 500) -> int:
    if not rows: return 0
    base = env("SUPABASE_URL") + "/rest/v1/hc_raw_record?on_conflict=record_type,record_uid"
    key = env("SUPABASE_SERVICE_ROLE_KEY")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    n = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        # Supabase REST wants JSONB payload as a stringified dict — but the
        # POST body is JSON, so payload-as-dict is fine; only stringify if
        # we hit a CHECK constraint.
        r = requests.post(base, headers=headers, data=json.dumps(chunk, default=str), timeout=120)
        if not r.ok:
            print(f"  upsert failed batch {i}-{i+len(chunk)}: HTTP {r.status_code} {r.text[:300]}", file=sys.stderr)
            sys.exit(1)
        n += len(chunk)
        print(f"  upserted {n}/{len(rows)}", file=sys.stderr)
    return n


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=None, help="Folder containing the 3 Health Sync subfolders")
    p.add_argument("--since", default="2026-05-24")
    p.add_argument("--until", default="2026-05-31")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json-out", default=None, help="Write parsed records to JSON instead of upserting")
    p.add_argument("--json-in", default=None, help="Skip CSV parsing, upsert directly from this JSON file")
    args = p.parse_args()

    # JSON-in mode: skip all CSV parsing, upsert pre-parsed records.
    if args.json_in:
        records = json.loads(Path(args.json_in).read_text(encoding="utf-8"))
        print(f"json-in: {len(records)} records loaded", file=sys.stderr)
        n = sb_upsert(records)
        print(f"done. {n} record(s) upserted into hc_raw_record", file=sys.stderr)
        return
    if not args.root:
        sys.exit("--root is required unless --json-in is used")
    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until)
    root = Path(args.root)

    # Locate the three folders by glob (filename has timestamp + UUID suffix).
    hr_dir = next((p for p in root.glob("*Fréquence*") if p.is_dir()), None)
    sleep_dir = next((p for p in root.glob("*Sommeil*") if p.is_dir()), None)
    if not hr_dir or not sleep_dir:
        sys.exit(f"could not find Health Sync HR or Sleep folders in {root}")
    # Folders contain a single inner subfolder
    hr_inner = next((p for p in hr_dir.iterdir() if p.is_dir()), hr_dir)
    sleep_inner = next((p for p in sleep_dir.iterdir() if p.is_dir()), sleep_dir)

    print(f"window: {since} → {until}", file=sys.stderr)
    print(f"HR folder:    {hr_inner}", file=sys.stderr)
    print(f"Sleep folder: {sleep_inner}", file=sys.stderr)

    hr_rows: list[dict] = []
    for csv_path in sorted(hr_inner.glob("*.csv")):
        # Heuristic: skip files that obviously don't cover the window.
        # Files are named "Fréquence cardiaque <mois> <année> ..." or "<week>-<year> ...".
        if "2026" not in csv_path.name and "2025" not in csv_path.name:
            continue
        rows = parse_hr_csv(csv_path, since, until)
        if rows:
            print(f"  HR {csv_path.name}: {len(rows)} rows in window", file=sys.stderr)
        hr_rows.extend(rows)
    # Dedup HR by record_uid (same timestamp can appear in monthly AND weekly file).
    seen_uids: set[str] = set()
    unique_hr: list[dict] = []
    for r in hr_rows:
        if r["record_uid"] in seen_uids: continue
        seen_uids.add(r["record_uid"]); unique_hr.append(r)
    print(f"HR total unique: {len(unique_hr)}", file=sys.stderr)

    sleep_rows: list[dict] = []
    for csv_path in sorted(sleep_inner.glob("*.csv")):
        if "2026" not in csv_path.name and "2025" not in csv_path.name:
            continue
        rows = parse_sleep_csv(csv_path, since, until)
        if rows:
            print(f"  Sleep {csv_path.name}: {len(rows)} session(s) in window", file=sys.stderr)
        sleep_rows.extend(rows)
    seen_uids = set()
    unique_sleep: list[dict] = []
    for r in sleep_rows:
        if r["record_uid"] in seen_uids: continue
        seen_uids.add(r["record_uid"]); unique_sleep.append(r)
    print(f"Sleep total unique: {len(unique_sleep)}", file=sys.stderr)

    if args.dry_run:
        print("DRY RUN — sample HR:", json.dumps(unique_hr[:2], indent=2, ensure_ascii=False))
        print("DRY RUN — sample Sleep:", json.dumps(unique_sleep[:2], indent=2, ensure_ascii=False))
        return

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.write_text(
            json.dumps(unique_hr + unique_sleep, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"wrote {len(unique_hr) + len(unique_sleep)} records to {out_path}", file=sys.stderr)
        return

    total = sb_upsert(unique_hr) + sb_upsert(unique_sleep)
    print(f"done. {total} record(s) upserted into hc_raw_record", file=sys.stderr)


if __name__ == "__main__":
    main()
