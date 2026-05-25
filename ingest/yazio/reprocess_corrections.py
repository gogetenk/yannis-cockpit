"""One-shot reprocessor: re-run the LLM second-opinion on existing
`yazio_correction` rows that were emitted by the deterministic rules.

For each active (reverted_at IS NULL) correction with source='rule', we:

  1. fetch the day's Yazio food items (best-effort);
  2. call `llm_sanity.review_correction` with that context;
  3. if the LLM proposes a different value or vetoes the rule, insert a NEW
     correction row with source='llm' and rule_key = f"llm_review_{old_rk}";
     mark the old row reverted_at = NOW();
  4. patch the affected `daily_features` row in place so analytics see the
     refined value immediately (no need to wait for the next cron tick).

Idempotency: if an active LLM correction with the synthesized rule_key
already exists on (date, nutrient_id), we skip the row entirely.

Required env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, ANTHROPIC_API_KEY,
YAZIO_EMAIL, YAZIO_PASSWORD (the last two are optional but strongly
recommended: without them the LLM only sees the raw_value, no food items).

CLI:
    python ingest/yazio/reprocess_corrections.py [--since 2024-01-01]
                                                 [--only-rule-key X]
                                                 [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import requests

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ingest.yazio import llm_sanity, sanitize  # noqa: E402

try:
    from ingest.yazio.fetch_food_items import fetch_food_items as _fetch_food_items
except Exception:  # pragma: no cover
    _fetch_food_items = None  # type: ignore[assignment]


# ---------- supabase helpers (small, self-contained) -------------------

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


def sb_patch(path: str, params: dict, body: dict) -> None:
    url = f"{env('SUPABASE_URL')}/rest/v1/{path}"
    h = {**sb_headers(), "Prefer": "return=minimal"}
    r = requests.patch(url, headers=h, params=params, data=json.dumps(body), timeout=60)
    if not r.ok:
        sys.exit(f"PATCH {path} failed {r.status_code}: {r.text[:500]}")


# ---------- fetch helpers ----------------------------------------------

_FOOD_CACHE: dict[str, list[dict] | None] = {}


def get_food_items(d_iso: str) -> list[dict] | None:
    if d_iso in _FOOD_CACHE:
        return _FOOD_CACHE[d_iso]
    if _fetch_food_items is None:
        _FOOD_CACHE[d_iso] = None
        return None
    if not (os.environ.get("YAZIO_EMAIL") and os.environ.get("YAZIO_PASSWORD")):
        _FOOD_CACHE[d_iso] = None
        return None
    try:
        items = _fetch_food_items(d_iso)
    except Exception as e:
        print(f"  fetch_food_items({d_iso}) failed: {e}", file=sys.stderr)
        items = None
    _FOOD_CACHE[d_iso] = items
    return items


# ---------- main pass --------------------------------------------------

NUTRIENT_TO_FIELD: dict[str, str] = {
    sanitize.NUT_ALCOHOL: "alcohol_g",
    sanitize.NUT_SODIUM: "sodium_mg",
    sanitize.NUT_FAT_SAT: "fat_sat_g",
    sanitize.NUT_SUGAR: "sugar_g",
    sanitize.NUT_FIBER: "fiber_g",
}


def _llm_rule_key(old_rule_key: str | None) -> str:
    return f"llm_review_{old_rule_key or 'unknown'}"


def _patch_daily_features(d_iso: str, nutrient_id: str, refined: float | None) -> None:
    field = NUTRIENT_TO_FIELD.get(nutrient_id)
    if field is None:
        return
    body: dict[str, Any] = {field: refined}
    # Re-derive pct_e_sat when saturated fat changes.
    if field == "fat_sat_g":
        rows = sb_get("daily_features", {"select": "kcal", "date": f"eq.{d_iso}"})
        kcal = (rows[0].get("kcal") if rows else None)
        if refined is None or not kcal or kcal <= 0:
            body["pct_e_sat"] = None
        else:
            body["pct_e_sat"] = round((float(refined) * 9.0) / float(kcal) * 100.0, 2)
    sb_patch("daily_features", {"date": f"eq.{d_iso}"}, body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2024-01-01", help="YYYY-MM-DD")
    parser.add_argument("--only-rule-key", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "[reprocess] ANTHROPIC_API_KEY not set -- llm_sanity will no-op "
            "and every correction will be left unchanged. Aborting.",
            file=sys.stderr,
        )
        return 2

    params = {
        "select": "id,date,nutrient_id,raw_value,sanitized_value,source,rule_key,reason",
        "source": "eq.rule",
        "reverted_at": "is.null",
        "date": f"gte.{args.since}",
        "order": "date.asc",
    }
    if args.only_rule_key:
        params["rule_key"] = f"eq.{args.only_rule_key}"
    corrections = sb_get("yazio_correction", params)
    print(f"[reprocess] loaded {len(corrections)} active rule-corrections "
          f"(since={args.since})", file=sys.stderr)

    # Existing LLM-reviewed rows -> dedupe key.
    llm_existing = sb_get(
        "yazio_correction",
        {
            "select": "date,nutrient_id,rule_key",
            "source": "eq.llm",
            "reverted_at": "is.null",
        },
    )
    seen: set[tuple[str, str, str]] = {
        (str(r["date"])[:10], r["nutrient_id"], r["rule_key"] or "")
        for r in llm_existing
    }

    # Build daily_kcal lookup once.
    dates_iso = sorted({str(c["date"])[:10] for c in corrections})
    kcal_rows: list[dict] = []
    if dates_iso:
        kcal_rows = sb_get(
            "daily_features",
            {"select": "date,kcal", "date": f"in.({','.join(dates_iso)})"},
        )
    kcal_by_date: dict[str, float | None] = {
        str(r["date"])[:10]: r.get("kcal") for r in kcal_rows
    }

    n_reviewed = 0
    n_refined = 0
    n_confirmed = 0
    n_vetoed = 0
    n_skipped = 0
    summary_rows: list[dict] = []

    for c in corrections:
        d_iso = str(c["date"])[:10]
        nid = c["nutrient_id"]
        old_rule_key = c.get("rule_key")
        new_rule_key = _llm_rule_key(old_rule_key)
        if (d_iso, nid, new_rule_key) in seen:
            print(
                f"  skip {d_iso} {nid}: LLM review already on file "
                f"(rule_key={new_rule_key})",
                file=sys.stderr,
            )
            n_skipped += 1
            continue

        raw_value = float(c["raw_value"])
        old_sanitized = c.get("sanitized_value")
        old_sanitized_f = float(old_sanitized) if old_sanitized is not None else None

        det_correction = sanitize.Correction(
            date=d_iso,
            nutrient_id=nid,
            raw_value=raw_value,
            sanitized_value=old_sanitized_f,
            source="rule",
            rule_key=old_rule_key or "",
            reason=c.get("reason") or "",
        )

        food_items = get_food_items(d_iso)
        daily_kcal = kcal_by_date.get(d_iso)

        print(
            f"  reviewing {d_iso} {nid} raw={raw_value} "
            f"old_sanitized={old_sanitized_f} kcal={daily_kcal} "
            f"items={len(food_items) if food_items else 0}",
            file=sys.stderr,
        )
        try:
            reviewed = llm_sanity.review_correction(det_correction, food_items, daily_kcal)
        except Exception as e:
            print(f"    LLM call exploded: {e}", file=sys.stderr)
            continue
        n_reviewed += 1

        changed = (
            reviewed.source == "llm"
            and (reviewed.sanitized_value != old_sanitized_f or reviewed.raw_value != raw_value)
        )

        if not changed:
            # LLM didn't escalate (or no key / API failure) -> leave as-is.
            print("    no change", file=sys.stderr)
            summary_rows.append({
                "date": d_iso,
                "nutrient_id": nid,
                "raw_value": raw_value,
                "old_sanitized": old_sanitized_f,
                "new_sanitized": old_sanitized_f,
                "verdict": "unchanged",
                "reason": c.get("reason"),
            })
            continue

        verdict: str
        new_sanitized = reviewed.sanitized_value
        if new_sanitized is None:
            verdict = "llm_confirms_drop"
            n_confirmed += 1
        elif new_sanitized == raw_value:
            verdict = "llm_veto_keeps_raw"
            n_vetoed += 1
        else:
            verdict = "llm_refine"
            n_refined += 1

        print(
            f"    -> {verdict}: sanitized={new_sanitized} "
            f"reason={reviewed.reason!r}",
            file=sys.stderr,
        )

        summary_rows.append({
            "date": d_iso,
            "nutrient_id": nid,
            "raw_value": raw_value,
            "old_sanitized": old_sanitized_f,
            "new_sanitized": new_sanitized,
            "verdict": verdict,
            "reason": reviewed.reason,
        })

        if args.dry_run:
            continue

        # Insert the new LLM correction row.
        new_row = {
            "date": d_iso,
            "nutrient_id": nid,
            "raw_value": raw_value,
            "sanitized_value": new_sanitized,
            "source": "llm",
            "rule_key": new_rule_key,
            "llm_model": reviewed.llm_model,
            "llm_confidence": reviewed.llm_confidence,
            "reason": reviewed.reason,
        }
        sb_post("yazio_correction", [new_row])
        seen.add((d_iso, nid, new_rule_key))

        # Revert the old rule correction (keep history).
        now_iso = datetime.now(timezone.utc).isoformat()
        sb_patch(
            "yazio_correction",
            {"id": f"eq.{c['id']}"},
            {"reverted_at": now_iso},
        )

        # Patch daily_features so the refined value is immediately visible.
        try:
            _patch_daily_features(d_iso, nid, new_sanitized)
        except Exception as e:
            print(f"    daily_features patch failed: {e}", file=sys.stderr)

    print("\n[reprocess] summary", file=sys.stderr)
    print(
        f"  reviewed   = {n_reviewed}\n"
        f"  refined    = {n_refined}\n"
        f"  confirmed  = {n_confirmed}\n"
        f"  vetoed     = {n_vetoed}\n"
        f"  skipped    = {n_skipped}\n"
        f"  dry_run    = {args.dry_run}",
        file=sys.stderr,
    )
    print("\n[reprocess] per-correction outcome", file=sys.stderr)
    for s in summary_rows:
        print(
            f"  {s['date']} {s['nutrient_id']:<22} raw={s['raw_value']:>10} "
            f"old={s['old_sanitized']!s:>8} new={s['new_sanitized']!s:>8} "
            f"-> {s['verdict']}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
