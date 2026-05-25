"""One-shot LLM enrichment backrun for daily_features micronutrients.

Walks every date in [since, until], pulls the day's yazio_meal rows, the
day's macros, and any existing micros / sources, then asks Haiku 4.5 to
estimate the missing micronutrients. Writes the filled values + their
`*_source = 'llm_estimate'` provenance back into daily_features.

This script is idempotent: by default it skips dates where every micro
column already has a non-null `_source` (meaning either Yazio provided
it, llm_sanity refined it, or a prior enrichment pass already wrote it).
Use `--force` to re-estimate everything.

CLI:
  python backrun_enrichment.py
  python backrun_enrichment.py --since 2023-05-25 --until 2024-05-25
  python backrun_enrichment.py --dry-run --since 2024-01-01
  python backrun_enrichment.py --force --since 2024-01-01

Env:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  ANTHROPIC_API_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ingest.yazio import enrich_estimation  # noqa: E402

MICRO_FIELDS = ("fat_sat_g", "sodium_mg", "sugar_g", "fiber_g", "alcohol_g")

# Haiku 4.5 pricing (per 1M tokens). Updated 2025-10.
PRICE_IN_PER_M = 1.0
PRICE_OUT_PER_M = 5.0
# Empirical average tokens per backrun call (small payload, ~5 entries out).
AVG_TOK_IN = 700
AVG_TOK_OUT = 250


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


def sb_patch_row(date_iso: str, patch: dict) -> None:
    url = (
        f"{env('SUPABASE_URL')}/rest/v1/daily_features"
        f"?date=eq.{date_iso}"
    )
    headers = {**sb_headers(), "Prefer": "return=minimal"}
    r = requests.patch(url, headers=headers, data=json.dumps(patch), timeout=60)
    if not r.ok:
        raise RuntimeError(
            f"PATCH daily_features {date_iso} failed {r.status_code}: {r.text[:300]}"
        )


def parse_args() -> argparse.Namespace:
    today = datetime.now(timezone.utc).date()
    p = argparse.ArgumentParser(description="LLM enrichment backrun.")
    p.add_argument(
        "--since",
        type=str,
        default=(today - timedelta(days=730)).isoformat(),
        help="ISO date floor (default = today - 730d).",
    )
    p.add_argument(
        "--until",
        type=str,
        default=today.isoformat(),
        help="ISO date ceiling (default = today).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Estimate but do not write back to daily_features.",
    )
    p.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="(default) Skip dates whose fat_sat_g_source IS NOT NULL.",
    )
    p.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Re-process every date in the window.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-estimate even fields with a non-null _source (overwrites llm_estimate, never yazio).",
    )
    p.add_argument(
        "--throttle",
        type=float,
        default=0.2,
        help="Seconds to sleep between LLM calls (anti rate-limit).",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of dates per progress log batch.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until)
    if until < since:
        sys.exit(f"--until {until} is before --since {since}")

    print(
        f"[backrun] window {since} -> {until} "
        f"(dry_run={args.dry_run} skip_existing={args.skip_existing} "
        f"force={args.force})",
        file=sys.stderr,
    )

    # Load daily_features in window (existing micros + sources).
    df_rows = sb_get(
        "daily_features",
        {
            "select": (
                "date,kcal,protein_g,carb_g,fat_g,"
                "fat_sat_g,fat_sat_g_source,"
                "sodium_mg,sodium_mg_source,"
                "sugar_g,sugar_g_source,"
                "fiber_g,fiber_g_source,"
                "alcohol_g,alcohol_g_source"
            ),
            "date": f"gte.{since.isoformat()}",
        },
    )
    df_by_date: dict[str, dict] = {}
    for r in df_rows:
        d_iso = str(r.get("date") or "")[:10]
        if not d_iso:
            continue
        try:
            d = date.fromisoformat(d_iso)
        except ValueError:
            continue
        if d > until:
            continue
        df_by_date[d_iso] = r

    # Load yazio_food_item_daily rows in window (richer than yazio_meal).
    food_rows = sb_get(
        "yazio_food_item_daily",
        {
            "select": "date,meal_slot,item_index,item_name,amount_g",
            "date": f"gte.{since.isoformat()}",
        },
    )
    food_items_by_date: dict[str, list[dict]] = defaultdict(list)
    for r in food_rows:
        d_iso = str(r.get("date") or "")[:10]
        if not d_iso:
            continue
        try:
            d = date.fromisoformat(d_iso)
        except ValueError:
            continue
        if d > until:
            continue
        food_items_by_date[d_iso].append(
            {
                "meal": r.get("meal_slot"),
                "meal_slot": r.get("meal_slot"),
                "item_index": r.get("item_index"),
                "name": r.get("item_name"),
                "amount_g": r.get("amount_g"),
            }
        )
    for d_iso in food_items_by_date:
        food_items_by_date[d_iso].sort(
            key=lambda x: (x.get("meal_slot") or "", x.get("item_index") or 0)
        )

    # Load yazio_meal rows in window.
    meals_rows = sb_get(
        "yazio_meal",
        {
            "select": "date,meal,kcal,protein_g,carb_g,fat_g",
            "date": f"gte.{since.isoformat()}",
        },
    )
    meals_by_date: dict[str, list[dict]] = defaultdict(list)
    for r in meals_rows:
        d_iso = str(r.get("date") or "")[:10]
        if not d_iso:
            continue
        try:
            d = date.fromisoformat(d_iso)
        except ValueError:
            continue
        if d > until:
            continue
        meals_by_date[d_iso].append(
            {
                "name": r.get("meal"),
                "meal": r.get("meal"),
                "kcal": r.get("kcal"),
                "protein_g": r.get("protein_g"),
                "carb_g": r.get("carb_g"),
                "fat_g": r.get("fat_g"),
            }
        )

    candidates = sorted(df_by_date.keys())
    print(
        f"[backrun] {len(candidates)} daily_features rows in window, "
        f"{sum(1 for d in candidates if meals_by_date.get(d))} have meal logs, "
        f"{sum(1 for d in candidates if food_items_by_date.get(d))} have food items",
        file=sys.stderr,
    )

    n_processed = 0
    n_skipped_existing = 0
    n_skipped_nomeals = 0
    n_llm_calls = 0
    n_nutrients_filled = 0
    per_field_filled: dict[str, int] = defaultdict(int)

    for i, d_iso in enumerate(candidates):
        row = df_by_date[d_iso]
        meals = meals_by_date.get(d_iso) or []
        food_items = food_items_by_date.get(d_iso) or []

        existing_micros = {f: row.get(f) for f in MICRO_FIELDS}
        existing_sources = {f: row.get(f"{f}_source") for f in MICRO_FIELDS}
        missing = [
            f for f in MICRO_FIELDS
            if existing_micros[f] is None
            and (args.force or existing_sources[f] is None)
        ]
        if not missing:
            n_skipped_existing += 1
            continue
        if not meals and not food_items:
            n_skipped_nomeals += 1
            continue

        daily_macros = {
            "kcal": row.get("kcal"),
            "protein_g": row.get("protein_g"),
            "carb_g": row.get("carb_g"),
            "fat_g": row.get("fat_g"),
        }

        # For force-mode, hide existing llm_estimate values from the
        # "do not overwrite Yazio" gate inside the estimator -- but keep
        # genuine yazio values as a fence.
        existing_for_llm = dict(existing_micros)
        if args.force:
            for f in MICRO_FIELDS:
                if existing_sources.get(f) in (None, "llm_estimate"):
                    existing_for_llm[f] = None

        try:
            estimates = enrich_estimation.estimate_day_micros(
                d_iso, meals, existing_for_llm, daily_macros,
                food_items=food_items,
            )
        except Exception as e:
            print(f"[backrun] LLM call failed for {d_iso}: {e}", file=sys.stderr)
            estimates = {}
        n_llm_calls += 1
        n_processed += 1

        patch: dict = {}
        for field, payload in estimates.items():
            if field not in MICRO_FIELDS:
                continue
            patch[field] = payload["value"]
            patch[f"{field}_source"] = payload["source"]
            n_nutrients_filled += 1
            per_field_filled[field] += 1
            # Re-derive pct_e_sat when SFA was set.
            if field == "fat_sat_g":
                kcal = row.get("kcal")
                if kcal and float(kcal) > 0 and payload["value"] is not None:
                    patch["pct_e_sat"] = round(
                        (float(payload["value"]) * 9.0) / float(kcal) * 100.0, 2
                    )

        if patch and not args.dry_run:
            try:
                sb_patch_row(d_iso, patch)
            except Exception as e:
                print(f"[backrun] patch {d_iso} failed: {e}", file=sys.stderr)

        if (i + 1) % args.batch_size == 0:
            print(
                f"[backrun] progress {i + 1}/{len(candidates)} "
                f"calls={n_llm_calls} filled={n_nutrients_filled}",
                file=sys.stderr,
            )

        if args.throttle > 0:
            time.sleep(args.throttle)

    est_cost = (
        n_llm_calls * AVG_TOK_IN / 1_000_000 * PRICE_IN_PER_M
        + n_llm_calls * AVG_TOK_OUT / 1_000_000 * PRICE_OUT_PER_M
    )
    print(
        "[backrun] DONE: "
        f"processed={n_processed} llm_calls={n_llm_calls} "
        f"filled={n_nutrients_filled} "
        f"(per-field: {dict(per_field_filled)}) "
        f"skipped_existing={n_skipped_existing} skipped_no_meals={n_skipped_nomeals} "
        f"dry_run={args.dry_run} estimated_cost_usd~${est_cost:.3f}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
