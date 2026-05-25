"""Pull Yazio consumed food items for a single date.

Extracted from `debug_one_day.py` so it can be reused by the LLM sanity layer
(see `ingest/yazio/llm_sanity.py`) and the reprocess script
(see `ingest/yazio/reprocess_corrections.py`).

Returns a list of dicts shaped:
    {
        "name": str,
        "amount_g": float,
        "meal": str,                       # breakfast/lunch/dinner/snack/...
        "nutrient_alcohol_per_100g": float | None,
        "kcal_per_100g": float | None,
        "sodium_per_100g_mg": float | None,
        "fat_g_per_100g": float | None,
        "carb_g_per_100g": float | None,
    }

Auth: prefer `token_path` if supplied; otherwise login via the
`YAZIO_EMAIL` / `YAZIO_PASSWORD` env vars. Requires the `yazio-exporter`
CLI on PATH (already installed by ingest/yazio/requirements.txt).

This module is import-safe: it shells out to the CLI only when called.
On any subprocess / parse failure it raises -- the caller is responsible
for catching and degrading gracefully (LLM still runs with food_items=None).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _run_cli(*args: str) -> None:
    # Hide raw output to keep logs readable when iterating over many dates.
    subprocess.run(
        ["yazio-exporter", *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def fetch_food_items(
    d: date | str,
    token_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return the consumed food items for one date.

    Parameters
    ----------
    d : a ``datetime.date`` or ISO ``YYYY-MM-DD`` string.
    token_path : optional path to an already-issued Yazio token file. If
        omitted, a fresh login is performed using YAZIO_EMAIL/YAZIO_PASSWORD.
    """
    target = d.isoformat() if isinstance(d, date) else str(d)[:10]

    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        days_path = tmp / "days.json"
        products_path = tmp / "products.json"

        if token_path is None:
            email = os.environ.get("YAZIO_EMAIL")
            password = os.environ.get("YAZIO_PASSWORD")
            if not email or not password:
                raise RuntimeError(
                    "fetch_food_items: no token_path provided and "
                    "YAZIO_EMAIL/YAZIO_PASSWORD not set"
                )
            token_path = tmp / "token.txt"
            _run_cli("login", email, password, "-o", str(token_path))

        _run_cli(
            "days",
            "-t", str(token_path),
            "-f", target, "-e", target,
            "-o", str(days_path),
            "--format", "json",
            "-w", "consumed,daily_summary",
        )
        _run_cli(
            "products",
            "-t", str(token_path),
            "--from-file", str(days_path),
            "-o", str(products_path),
            "--format", "json",
        )

        days = json.loads(days_path.read_text())
        products = json.loads(products_path.read_text()).get("products", {})

    day = days.get(target, {})
    consumed = day.get("consumed", {}) or {}
    items = consumed.get("products", []) or []

    out: list[dict[str, Any]] = []
    for it in items:
        pid = it.get("product_id")
        amount = it.get("amount") or 0
        meal = it.get("daytime", "?")
        p = products.get(pid) or {}
        name = p.get("name", f"<unknown {pid}>")
        nutr = p.get("nutrients", {}) or {}

        # Sodium in Yazio is stored as g/100g under `mineral.sodium`.
        # Convert to mg/100g for the LLM payload.
        sodium_per_100g = nutr.get("mineral.sodium") or nutr.get("nutrient.sodium")
        sodium_per_100g_mg: float | None = None
        if sodium_per_100g is not None:
            try:
                sodium_per_100g_mg = float(sodium_per_100g) * 1000.0
            except (TypeError, ValueError):
                sodium_per_100g_mg = None

        def _num(x: Any) -> float | None:
            if x is None:
                return None
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        # Cholesterol is stored in g/100g in some Yazio regions; convert to mg.
        chol_per_100g = nutr.get("nutrient.cholesterol")
        chol_per_100g_mg: float | None = None
        if chol_per_100g is not None:
            try:
                chol_per_100g_mg = float(chol_per_100g) * 1000.0
            except (TypeError, ValueError):
                chol_per_100g_mg = None

        out.append({
            "name": name,
            "amount_g": float(amount or 0),
            "meal": meal,
            "nutrient_alcohol_per_100g": _num(nutr.get("nutrient.alcohol")),
            "kcal_per_100g": _num(nutr.get("energy.energy")),
            "sodium_per_100g_mg": sodium_per_100g_mg,
            "fat_g_per_100g": _num(nutr.get("nutrient.fat")),
            "fat_sat_per_100g": _num(nutr.get("nutrient.saturated")),
            "carb_g_per_100g": _num(nutr.get("nutrient.carb")),
            "sugar_per_100g": _num(nutr.get("nutrient.sugar")),
            "fiber_per_100g": _num(
                nutr.get("nutrient.fiber") or nutr.get("nutrient.dietaryfiber")
            ),
            "protein_g_per_100g": _num(nutr.get("nutrient.protein")),
            "cholesterol_per_100g_mg": chol_per_100g_mg,
            "product_id": pid,
        })

    _log(f"  (fetch_food_items: {target} -> {len(out)} items)")
    return out


def parse_food_items_from_days(
    days: dict[str, Any],
    products: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Parse a multi-date `days.json` payload into food items grouped by date.

    Use this when you already have the bulk `days` + `products` JSON from a
    single CLI window invocation (cheaper than calling :func:`fetch_food_items`
    once per date).
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for iso_date, day in (days or {}).items():
        consumed = (day or {}).get("consumed") or {}
        items = consumed.get("products") or []
        rows: list[dict[str, Any]] = []
        for it in items:
            pid = it.get("product_id")
            amount = it.get("amount") or 0
            meal = it.get("daytime", "?")
            p = (products or {}).get(pid) or {}
            name = p.get("name", f"<unknown {pid}>")
            nutr = p.get("nutrients", {}) or {}

            sodium_per_100g = nutr.get("mineral.sodium") or nutr.get("nutrient.sodium")
            sodium_per_100g_mg: float | None = None
            if sodium_per_100g is not None:
                try:
                    sodium_per_100g_mg = float(sodium_per_100g) * 1000.0
                except (TypeError, ValueError):
                    sodium_per_100g_mg = None

            chol_per_100g = nutr.get("nutrient.cholesterol")
            chol_per_100g_mg: float | None = None
            if chol_per_100g is not None:
                try:
                    chol_per_100g_mg = float(chol_per_100g) * 1000.0
                except (TypeError, ValueError):
                    chol_per_100g_mg = None

            def _num(x: Any) -> float | None:
                if x is None:
                    return None
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return None

            rows.append({
                "name": name,
                "amount_g": float(amount or 0),
                "meal": meal,
                "nutrient_alcohol_per_100g": _num(nutr.get("nutrient.alcohol")),
                "kcal_per_100g": _num(nutr.get("energy.energy")),
                "sodium_per_100g_mg": sodium_per_100g_mg,
                "fat_g_per_100g": _num(nutr.get("nutrient.fat")),
                "fat_sat_per_100g": _num(nutr.get("nutrient.saturated")),
                "carb_g_per_100g": _num(nutr.get("nutrient.carb")),
                "sugar_per_100g": _num(nutr.get("nutrient.sugar")),
                "fiber_per_100g": _num(
                    nutr.get("nutrient.fiber") or nutr.get("nutrient.dietaryfiber")
                ),
                "protein_g_per_100g": _num(nutr.get("nutrient.protein")),
                "cholesterol_per_100g_mg": chol_per_100g_mg,
                "product_id": pid,
            })
        out[iso_date] = rows
    return out


if __name__ == "__main__":  # pragma: no cover - manual sanity
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()
    items = fetch_food_items(args.date)
    print(json.dumps(items, indent=2, ensure_ascii=False))
