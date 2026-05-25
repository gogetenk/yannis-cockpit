"""One-shot retro scan for `alcohol_false_zero` corrections.

For every Yazio day between --since (default 2024-01-01) and today, we:

  1. fetch the day's food_items via `fetch_food_items.fetch_food_items`;
  2. run `sanitize.detect_alcohol_false_zero(food_items)` -- a regex/threshold
     check that fires when an item name matches a beer/wine/spirit pattern
     while its per-100g alcohol is reported as ~0;
  3. if the rule fires, call `llm_sanity.review_correction` so Haiku can
     estimate the day's total ethanol from the matched items;
  4. upsert a row in `yazio_correction` (idempotent: skip if an active
     `alcohol_false_zero` row already exists on (date, nutrient.alcohol)
     OR if its synthesized `llm_review_alcohol_false_zero` LLM row exists).

The script does NOT itself trigger `build_daily_features`. The workflow
that wraps it does, so corrections are propagated to `daily_features` in
the same run.

Required env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ANTHROPIC_API_KEY,
YAZIO_EMAIL, YAZIO_PASSWORD.

CLI:
    python ingest/yazio/scan_false_zero_alcohol.py [--since 2024-01-01]
                                                   [--until 2026-12-31]
                                                   [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ingest.yazio import llm_sanity, sanitize  # noqa: E402
from ingest.yazio.fetch_food_items import fetch_food_items  # noqa: E402

RULE_KEY = "alcohol_false_zero"
LLM_RULE_KEY = "llm_review_alcohol_false_zero"


# ---------- supabase helpers -------------------------------------------

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


def sb_get(path: str, params: dict | None = None) -> list[dict]:
    out: list[dict] = []
    page = 1000
    offset = 0
    while True:
        h = sb_headers()
        h["Range-Unit"] = "items"
        h["Range"] = f"{offset}-{offset + page - 1}"
        r = requests.get(
            f"{env('SUPABASE_URL')}/rest/v1/{path}",
            headers=h,
            params=params,
            timeout=60,
        )
        r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return out


def sb_post(path: str, body: list[dict] | dict) -> None:
    url = f"{env('SUPABASE_URL')}/rest/v1/{path}"
    h = {**sb_headers(), "Prefer": "return=minimal"}
    r = requests.post(url, headers=h, data=json.dumps(body), timeout=60)
    if not r.ok:
        sys.exit(f"POST {path} failed {r.status_code}: {r.text[:500]}")


# ---------- main scan --------------------------------------------------

def _parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _iter_dates(d_from: date, d_to: date):
    cur = d_from
    one_day = timedelta(days=1)
    while cur <= d_to:
        yield cur
        cur += one_day


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2024-01-01", help="YYYY-MM-DD")
    parser.add_argument(
        "--until",
        default=date.today().isoformat(),
        help="YYYY-MM-DD (default = today)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
                "ANTHROPIC_API_KEY", "YAZIO_EMAIL", "YAZIO_PASSWORD"):
        if not os.environ.get(var):
            print(f"[scan] env var missing: {var} -- aborting", file=sys.stderr)
            return 2

    d_from = _parse_date(args.since)
    d_to = _parse_date(args.until)
    print(
        f"[scan] window {d_from.isoformat()} -> {d_to.isoformat()} "
        f"dry_run={args.dry_run}",
        file=sys.stderr,
    )

    # Dates that already have a yazio_day row -- we only scan those, to
    # avoid burning API quota on days the user never logged.
    yazio_days = sb_get(
        "yazio_day",
        {
            "select": "date",
            "date": f"gte.{d_from.isoformat()}",
            "order": "date.asc",
        },
    )
    logged_dates = {str(r["date"])[:10] for r in yazio_days}
    print(f"[scan] {len(logged_dates)} dates have yazio_day rows", file=sys.stderr)

    # Dedupe set: skip dates with an active alcohol_false_zero correction
    # (rule or LLM-reviewed twin).
    existing = sb_get(
        "yazio_correction",
        {
            "select": "date,rule_key",
            "nutrient_id": "eq.nutrient.alcohol",
            "reverted_at": "is.null",
            "date": f"gte.{d_from.isoformat()}",
        },
    )
    seen_dates = {
        str(r["date"])[:10]
        for r in existing
        if r.get("rule_key") in (RULE_KEY, LLM_RULE_KEY)
    }
    print(f"[scan] {len(seen_dates)} dates already have an active false-zero correction", file=sys.stderr)

    # Build daily_kcal lookup (for LLM context).
    kcal_rows = sb_get(
        "daily_features",
        {
            "select": "date,kcal",
            "date": f"gte.{d_from.isoformat()}",
        },
    )
    kcal_by_date: dict[str, float | None] = {
        str(r["date"])[:10]: r.get("kcal") for r in kcal_rows
    }

    n_scanned = 0
    n_fired = 0
    n_emitted = 0
    n_skipped = 0
    samples: list[dict] = []

    for d in _iter_dates(d_from, d_to):
        d_iso = d.isoformat()
        if d_iso not in logged_dates:
            continue
        if d_iso in seen_dates:
            n_skipped += 1
            continue

        n_scanned += 1
        try:
            items = fetch_food_items(d_iso)
        except Exception as e:
            print(f"  [{d_iso}] fetch failed: {e}", file=sys.stderr)
            continue

        det = sanitize.detect_alcohol_false_zero(d_iso, items)
        if det is None:
            continue
        n_fired += 1
        print(f"  [{d_iso}] rule fired: {det.reason}", file=sys.stderr)

        daily_kcal = kcal_by_date.get(d_iso)
        try:
            reviewed = llm_sanity.review_correction(det, items, daily_kcal)
        except Exception as e:
            print(f"    LLM failed: {e}", file=sys.stderr)
            reviewed = det

        # Pick final sanitized_value: prefer LLM-refined, else None (drop).
        final_value = reviewed.sanitized_value
        # Clamp into [0, 150] just in case.
        if final_value is not None:
            try:
                final_value = max(0.0, min(150.0, float(final_value)))
            except (TypeError, ValueError):
                final_value = None

        is_llm = reviewed.source == "llm"
        out_rule_key = LLM_RULE_KEY if is_llm else RULE_KEY
        row = {
            "date": d_iso,
            "nutrient_id": sanitize.NUT_ALCOHOL,
            "raw_value": 0.0,
            "sanitized_value": final_value,
            "source": "llm" if is_llm else "rule",
            "rule_key": out_rule_key,
            "llm_model": reviewed.llm_model,
            "llm_confidence": reviewed.llm_confidence,
            "reason": reviewed.reason,
        }
        samples.append({
            "date": d_iso,
            "reason": reviewed.reason,
            "llm_value": final_value,
            "source": row["source"],
        })
        print(
            f"    -> source={row['source']} sanitized={final_value} "
            f"reason={reviewed.reason!r}",
            file=sys.stderr,
        )

        if args.dry_run:
            n_emitted += 1
            continue

        sb_post("yazio_correction", [row])
        n_emitted += 1
        seen_dates.add(d_iso)

    print("\n[scan] summary", file=sys.stderr)
    print(
        f"  window      = {d_from} -> {d_to}\n"
        f"  yazio_days  = {len(logged_dates)}\n"
        f"  scanned     = {n_scanned}\n"
        f"  fired       = {n_fired}\n"
        f"  emitted     = {n_emitted}\n"
        f"  skipped     = {n_skipped}\n"
        f"  dry_run     = {args.dry_run}",
        file=sys.stderr,
    )
    if samples:
        print("\n[scan] sample (first 10):", file=sys.stderr)
        for s in samples[:10]:
            print(
                f"  {s['date']} src={s['source']} val={s['llm_value']} "
                f"-- {s['reason'][:120]}",
                file=sys.stderr,
            )

    if not args.dry_run and n_emitted > 0:
        print(
            "\n[scan] re-run `build_daily_features --since "
            f"{d_from.isoformat()}` to propagate these corrections.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    # Reference timezone for completeness (not strictly used).
    _ = timezone.utc
    raise SystemExit(main())
