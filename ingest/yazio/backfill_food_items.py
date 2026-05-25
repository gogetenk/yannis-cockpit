"""One-shot historical backfill for `yazio_food_item_daily`.

Walks every date in [--since, --until] day-by-day, calls the Yazio API to
fetch the consumed items + product nutrient profiles, and persists them into
`yazio_food_item_daily`. Designed to populate the full user history (2022+)
in one workflow_dispatch run.

Idempotent: with --skip-existing (default) the script reads which dates are
already present in `yazio_food_item_daily` once at startup and skips them.
Dates that legitimately have 0 items (user did not log anything) are NOT
persisted -- the absence of a row is the signal "non loggé".

Env:
  YAZIO_EMAIL, YAZIO_PASSWORD       Yazio account creds
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ingest.yazio.fetch_food_items import parse_food_items_from_days  # noqa: E402

ALLOWED_MEAL_SLOTS = {"breakfast", "lunch", "dinner", "snack"}


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"missing env var: {name}")
    return v


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


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


def existing_dates() -> set[str]:
    rows = sb_get("yazio_food_item_daily", {"select": "date"})
    return {str(r.get("date") or "")[:10] for r in rows if r.get("date")}


def run_cli(*args: str) -> None:
    subprocess.run(
        ["yazio-exporter", *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def login_once(out_dir: Path) -> Path:
    token = out_dir / "token.txt"
    run_cli("login", env("YAZIO_EMAIL"), env("YAZIO_PASSWORD"), "-o", str(token))
    return token


def fetch_one_date(token: Path, tmp: Path, d: date) -> list[dict]:
    """Return yazio_food_item_daily rows for a single date (empty if none)."""
    target = d.isoformat()
    days_path = tmp / f"days_{target}.json"
    products_path = tmp / f"products_{target}.json"
    try:
        run_cli(
            "days",
            "-t", str(token),
            "-f", target, "-e", target,
            "-o", str(days_path),
            "--format", "json",
            "-w", "consumed,daily_summary",
        )
        run_cli(
            "products",
            "-t", str(token),
            "--from-file", str(days_path),
            "-o", str(products_path),
            "--format", "json",
        )
    except subprocess.CalledProcessError as e:
        log(f"  {target}: yazio-exporter failed ({e}); skipping")
        return []

    try:
        days = json.loads(days_path.read_text())
        products = json.loads(products_path.read_text()).get("products", {})
    except Exception as e:
        log(f"  {target}: parse failed ({e}); skipping")
        return []
    finally:
        for p in (days_path, products_path):
            try:
                p.unlink()
            except OSError:
                pass

    by_date = parse_food_items_from_days(days, products)
    items = by_date.get(target) or []
    per_slot: dict[str, int] = {}
    rows: list[dict] = []
    for it in items:
        slot = it.get("meal")
        if slot not in ALLOWED_MEAL_SLOTS:
            continue
        idx = per_slot.get(slot, 0)
        per_slot[slot] = idx + 1
        rows.append({
            "date": target,
            "meal_slot": slot,
            "item_index": idx,
            "item_name": it.get("name") or "<unknown>",
            "amount_g": it.get("amount_g"),
            "product_id": it.get("product_id"),
            "kcal_per_100g": it.get("kcal_per_100g"),
            "protein_per_100g": it.get("protein_g_per_100g"),
            "carb_per_100g": it.get("carb_g_per_100g"),
            "fat_per_100g": it.get("fat_g_per_100g"),
            "fat_sat_per_100g": it.get("fat_sat_per_100g"),
            "sodium_per_100g_mg": it.get("sodium_per_100g_mg"),
            "sugar_per_100g": it.get("sugar_per_100g"),
            "fiber_per_100g": it.get("fiber_per_100g"),
            "alcohol_per_100g": it.get("nutrient_alcohol_per_100g"),
            "cholesterol_per_100g_mg": it.get("cholesterol_per_100g_mg"),
        })
    return rows


def persist(rows: list[dict]) -> None:
    """Delete-then-insert the (single) date in `rows`."""
    if not rows:
        return
    dates = sorted({r["date"] for r in rows})
    in_filter = ",".join(dates)
    base = f"{env('SUPABASE_URL')}/rest/v1/yazio_food_item_daily"
    headers = {**sb_headers(), "Prefer": "return=minimal"}
    r = requests.delete(f"{base}?date=in.({in_filter})", headers=headers, timeout=60)
    if not r.ok:
        raise RuntimeError(
            f"delete yazio_food_item_daily failed {r.status_code}: {r.text[:300]}"
        )
    r = requests.post(base, headers=headers, data=json.dumps(rows), timeout=60)
    if not r.ok:
        raise RuntimeError(
            f"insert yazio_food_item_daily failed {r.status_code}: {r.text[:300]}"
        )


def parse_args() -> argparse.Namespace:
    today = datetime.now(timezone.utc).date()
    p = argparse.ArgumentParser(description="Backfill yazio_food_item_daily.")
    p.add_argument("--since", default="2022-01-01", help="ISO date (default 2022-01-01).")
    p.add_argument("--until", default=today.isoformat(), help="ISO date (default today).")
    p.add_argument("--dry-run", action="store_true", help="Fetch + log but do not persist.")
    p.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="(default) Skip dates already present in yazio_food_item_daily.",
    )
    p.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Re-fetch every date in the window (overwrites in place).",
    )
    p.add_argument("--throttle", type=float, default=0.3, help="Sleep between dates (s).")
    p.add_argument("--progress-every", type=int, default=25)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until)
    if until < since:
        sys.exit(f"--until {until} is before --since {since}")

    log(
        f"[backfill_food] window {since} -> {until} "
        f"(dry_run={args.dry_run} skip_existing={args.skip_existing} "
        f"throttle={args.throttle}s)"
    )

    skip: set[str] = set()
    if args.skip_existing:
        try:
            skip = existing_dates()
            log(f"[backfill_food] {len(skip)} date(s) already on file -- will skip")
        except Exception as e:
            log(f"[backfill_food] could not read existing dates: {e}")

    n_dates = (until - since).days + 1
    log(f"[backfill_food] {n_dates} date(s) to walk")

    n_processed = 0
    n_skipped = 0
    n_empty = 0
    n_persisted_items = 0
    n_dates_with_items = 0

    with tempfile.TemporaryDirectory(prefix="yazio-backfill-") as tmp_s:
        tmp = Path(tmp_s)
        token = login_once(tmp)

        d = since
        i = 0
        while d <= until:
            i += 1
            d_iso = d.isoformat()
            if d_iso in skip:
                n_skipped += 1
                d += timedelta(days=1)
                continue
            rows = fetch_one_date(token, tmp, d)
            if not rows:
                n_empty += 1
            else:
                n_dates_with_items += 1
                n_persisted_items += len(rows)
                if not args.dry_run:
                    try:
                        persist(rows)
                    except Exception as e:
                        log(f"  {d_iso}: persist failed: {e}")
            n_processed += 1
            if i % args.progress_every == 0:
                log(
                    f"[backfill_food] {i}/{n_dates} done "
                    f"(processed={n_processed} skipped={n_skipped} "
                    f"items={n_persisted_items} non_logged={n_empty})"
                )
            if args.throttle > 0:
                time.sleep(args.throttle)
            d += timedelta(days=1)

    log(
        "[backfill_food] DONE: "
        f"processed={n_processed} skipped_existing={n_skipped} "
        f"dates_with_items={n_dates_with_items} non_logged={n_empty} "
        f"items_persisted={n_persisted_items} dry_run={args.dry_run}"
    )


if __name__ == "__main__":
    main()
