"""
Daily Yazio → Supabase ingest.

Orchestrates the yazio-exporter CLI (login + days + weight + nutrients) over
a rolling window, then upserts into:
- yazio_day                    (one row per date — kcal/macros totals)
- yazio_meal                   (one row per date × meal — kept for future
                                meal-pattern analysis even though the
                                cockpit reads day totals only)
- yazio_micronutrient_daily    (one row per date × nutrient)

Per-meal aggregation in Yazio is noisy (items can land in the wrong slot
when logged outside their canonical time window), so the day total is the
authoritative signal for the cockpit; meal rows are stored for downstream
processing.

Idempotent on the natural keys. Designed for the GitHub Actions daily cron;
runs equally well locally if the env vars are set.

Env vars:
    YAZIO_EMAIL                Yazio account email
    YAZIO_PASSWORD             Yazio account password
    SUPABASE_URL               https://<ref>.supabase.co
    SUPABASE_SERVICE_ROLE_KEY  service_role secret (sb_secret_*)
    INGEST_DAYS                Optional, default 14 (rolling window in days)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import requests


REQUIRED_ENV = ("YAZIO_EMAIL", "YAZIO_PASSWORD", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
MEAL_KEYS = ("breakfast", "lunch", "dinner", "snack")

# Yazio namespaces nutrient keys in API responses (e.g. "energy.energy",
# "nutrient.protein"). Our column-friendly names map onto these.
NUTRIENT_KEY = {
    "energy": "energy.energy",
    "protein": "nutrient.protein",
    "carb": "nutrient.carb",
    "fat": "nutrient.fat",
}


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"missing env var: {name}")
    return value


def run(cmd: list[str]) -> None:
    """Run a subprocess, fail loudly on non-zero exit, redact creds in logs."""
    redacted = [
        "***" if i > 1 and cmd[0] == "yazio-exporter" and cmd[1] == "login" else x
        for i, x in enumerate(cmd)
    ]
    print(f"→ {' '.join(redacted)}", file=sys.stderr)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.stderr.write(res.stdout)
        sys.stderr.write(res.stderr)
        sys.exit(f"{cmd[0]} {cmd[1]} failed (exit {res.returncode})")


def run_exporter(out_dir: Path, days_window: int) -> None:
    """Run login + days + weight + nutrients into out_dir for the trailing window."""
    start = (date.today() - timedelta(days=days_window)).isoformat()
    end = date.today().isoformat()
    token = out_dir / "token.txt"
    days_json = out_dir / "days.json"
    weight_json = out_dir / "weight.json"
    nutrients_json = out_dir / "nutrients.json"

    run(["yazio-exporter", "login", env("YAZIO_EMAIL"), env("YAZIO_PASSWORD"), "-o", str(token)])
    run([
        "yazio-exporter", "days",
        "-t", str(token), "-f", start, "-e", end,
        "-o", str(days_json), "--format", "json",
    ])
    run([
        "yazio-exporter", "weight",
        "-t", str(token), "-f", start, "-e", end,
        "-o", str(weight_json), "--format", "json",
    ])
    run([
        "yazio-exporter", "nutrients",
        "-t", str(token), "-f", start, "-e", end,
        "-o", str(nutrients_json), "--format", "json",
    ])


def upsert(rows: list[dict], table: str, on_conflict: str) -> None:
    """POST rows to Supabase PostgREST with upsert semantics."""
    if not rows:
        print(f"  {table}: nothing to upsert", file=sys.stderr)
        return
    url = f"{env('SUPABASE_URL')}/rest/v1/{table}?on_conflict={on_conflict}"
    key = env("SUPABASE_SERVICE_ROLE_KEY")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    r = requests.post(url, headers=headers, data=json.dumps(rows), timeout=30)
    if not r.ok:
        sys.exit(f"upsert {table} failed {r.status_code}: {r.text[:500]}")
    print(f"  {table}: {len(rows)} rows upserted", file=sys.stderr)


def _sum_meal_nutrients(meals: dict, key: str) -> float | None:
    """Aggregate one nutrient (energy/protein/carb/fat) across the 4 meals."""
    api_key = NUTRIENT_KEY[key]
    total = 0.0
    found = False
    for meal_name in MEAL_KEYS:
        nutrients = ((meals or {}).get(meal_name) or {}).get("nutrients") or {}
        if api_key in nutrients and nutrients[api_key] is not None:
            total += float(nutrients[api_key])
            found = True
    return total if found else None


def parse_days(out_dir: Path, weight_by_date: dict[str, float]) -> tuple[list[dict], list[dict]]:
    """Project days.json + weight.json into (yazio_day rows, yazio_meal rows)."""
    path = out_dir / "days.json"
    if not path.exists():
        return [], []
    raw = json.loads(path.read_text())
    days, meals = [], []
    for iso_date, day in raw.items():
        summary = day.get("daily_summary") or {}
        meal_map = summary.get("meals") or {}
        water = day.get("water") or {}
        days.append(
            {
                "date": iso_date,
                "kcal": _sum_meal_nutrients(meal_map, "energy"),
                "protein_g": _sum_meal_nutrients(meal_map, "protein"),
                "carb_g": _sum_meal_nutrients(meal_map, "carb"),
                "fat_g": _sum_meal_nutrients(meal_map, "fat"),
                "water_ml": water.get("water_intake"),
                "steps": summary.get("steps"),
                "activity_kcal": summary.get("activity_energy"),
                "weight_kg": weight_by_date.get(iso_date),
                "body_fat_pct": None,  # not exposed by `weight` subcommand
                "source": day,
            }
        )
        for meal_name in MEAL_KEYS:
            m = (meal_map.get(meal_name) or {}).get("nutrients") or {}
            if not m:
                continue
            meals.append({
                "date": iso_date,
                "meal": meal_name,
                "kcal": m.get(NUTRIENT_KEY["energy"]),
                "protein_g": m.get(NUTRIENT_KEY["protein"]),
                "carb_g": m.get(NUTRIENT_KEY["carb"]),
                "fat_g": m.get(NUTRIENT_KEY["fat"]),
            })
    return days, meals


def parse_weight(out_dir: Path) -> dict[str, float]:
    """weight.json = {date: weight_kg}. Return as plain dict."""
    path = out_dir / "weight.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def parse_micronutrients(out_dir: Path) -> list[dict]:
    """nutrients.json = {nutrient_id: {date: value}}. Flatten to rows."""
    path = out_dir / "nutrients.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    rows = []
    for nutrient_id, by_date in raw.items():
        if not isinstance(by_date, dict):
            continue
        for iso_date, value in by_date.items():
            if value is None:
                continue
            rows.append({"date": iso_date, "nutrient_id": nutrient_id, "value": value})
    return rows


def main() -> None:
    for name in REQUIRED_ENV:
        env(name)
    days_window = int(os.environ.get("INGEST_DAYS", "14"))
    with tempfile.TemporaryDirectory(prefix="yazio-ingest-") as tmp:
        out_dir = Path(tmp)
        run_exporter(out_dir, days_window)
        weights = parse_weight(out_dir)
        day_rows, meal_rows = parse_days(out_dir, weights)
        micro_rows = parse_micronutrients(out_dir)
        upsert(day_rows, "yazio_day", "date")
        upsert(meal_rows, "yazio_meal", "date,meal")
        upsert(micro_rows, "yazio_micronutrient_daily", "date,nutrient_id")
    print("done", file=sys.stderr)


if __name__ == "__main__":
    main()
