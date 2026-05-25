"""Insight detection orchestrator.

Loads `daily_features` (last 200 days) from Supabase, runs all detectors,
UPSERTs candidates into `insight` by hash_dedup, and marks any previously-
active insight whose hash_dedup wasn't re-detected this run as inactive
(auto-expire).

Env:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  TODAY (optional ISO override)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

import pandas as pd
import requests

from .detectors import (
    budget_alcohol,
    budget_saturated_fat,
    mensink_ldl_projection,
    protein_deficit_streak,
    wegovy_adherence,
    wegovy_effect,
)
from .scoring import InsightCandidate

LOOKBACK_DAYS = 200


class SupabaseClient:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.key = key
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def request(
        self,
        method: str,
        table: str,
        params: dict[str, str] | None = None,
        body: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        url = f"{self.url}/rest/v1/{table}"
        if params:
            url = f"{url}?{urlencode(params)}"
        h = dict(self.headers)
        if extra_headers:
            h.update(extra_headers)
        r = requests.request(method, url, headers=h, json=body, timeout=30)
        if not r.ok:
            raise RuntimeError(f"{method} {table} -> {r.status_code}: {r.text[:400]}")
        if not r.text:
            return []
        try:
            data = r.json()
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else [data]


def load_daily_features(sb: SupabaseClient, today: date) -> pd.DataFrame:
    since = (pd.Timestamp(today) - pd.Timedelta(days=LOOKBACK_DAYS)).date().isoformat()
    try:
        rows = sb.request(
            "GET",
            "daily_features",
            params={
                "select": "*",
                "date": f"gte.{since}",
                "order": "date.asc",
            },
        )
    except RuntimeError as e:
        msg = str(e)
        if "PGRST205" in msg or "does not exist" in msg or "404" in msg:
            print(
                "[insights] daily_features table not found yet — skipping daily-feature detectors",
                file=sys.stderr,
            )
            return pd.DataFrame()
        raise
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def run_detectors(df: pd.DataFrame, today: date, sb: SupabaseClient) -> list[InsightCandidate]:
    candidates: list[InsightCandidate] = []
    detector_runs = [
        ("budget_saturated_fat", lambda: budget_saturated_fat.detect(df, today)),
        ("budget_alcohol", lambda: budget_alcohol.detect(df, today)),
        ("protein_deficit_streak", lambda: protein_deficit_streak.detect(df, today)),
        ("mensink_ldl_projection", lambda: mensink_ldl_projection.detect(df, today, sb)),
        ("wegovy_effect", lambda: wegovy_effect.detect(df, today)),
        ("wegovy_adherence", lambda: wegovy_adherence.detect(today, sb)),
    ]
    for name, fn in detector_runs:
        try:
            out = fn() or []
        except Exception as exc:
            print(f"[insights] detector {name} crashed: {exc}", file=sys.stderr)
            continue
        print(f"[insights] {name}: {len(out)} candidate(s)", file=sys.stderr)
        candidates.extend(out)
    return candidates


def upsert_candidates(sb: SupabaseClient, candidates: list[InsightCandidate]) -> list[str]:
    """UPSERT each candidate by hash_dedup. Returns hashes seen this run."""
    seen: list[str] = []
    for c in candidates:
        h = c.hash_dedup()
        seen.append(h)
        payload = {
            "detector_key": c.detector_key,
            "family": c.family,
            "severity": c.severity,
            "score": c.score(),
            "title": c.title,
            "body": c.body,
            "metric_keys": c.metric_keys,
            "data": c.data,
            "link_href": c.link_href,
            "active": True,
            "superseded_by": None,
            "hash_dedup": h,
            "detected_at": datetime.utcnow().isoformat() + "Z",
        }
        sb.request(
            "POST",
            "insight",
            params={"on_conflict": "hash_dedup"},
            body=payload,
            extra_headers={
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
    return seen


def expire_stale(sb: SupabaseClient, seen_hashes: list[str]) -> int:
    """Mark active insights whose hash_dedup wasn't re-detected as inactive."""
    rows = sb.request("GET", "insight", params={"select": "id,hash_dedup", "active": "eq.true"})
    seen_set = set(seen_hashes)
    stale_ids = [r["id"] for r in rows if r["hash_dedup"] not in seen_set]
    if not stale_ids:
        return 0
    # PATCH each (small N expected); could batch with in.() filter.
    in_list = ",".join(f"\"{i}\"" for i in stale_ids)
    sb.request(
        "PATCH",
        "insight",
        params={"id": f"in.({in_list})"},
        body={"active": False},
        extra_headers={"Prefer": "return=minimal"},
    )
    return len(stale_ids)


def main() -> int:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required", file=sys.stderr)
        return 2
    today_iso = os.environ.get("TODAY") or date.today().isoformat()
    today = date.fromisoformat(today_iso)

    sb = SupabaseClient(url, key)
    df = load_daily_features(sb, today)
    print(f"[insights] loaded daily_features rows={len(df)}", file=sys.stderr)

    candidates = run_detectors(df, today, sb)
    print(f"[insights] total candidates={len(candidates)}", file=sys.stderr)

    seen = upsert_candidates(sb, candidates)
    n_stale = expire_stale(sb, seen)
    print(f"[insights] upserted={len(seen)} expired={n_stale}", file=sys.stderr)

    # final active count
    active = sb.request("GET", "insight", params={"select": "id", "active": "eq.true"})
    print(f"[insights] active insights now: {len(active)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
