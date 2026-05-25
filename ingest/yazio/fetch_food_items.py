"""Pull Yazio consumed food items for a single date.

Extracted from `debug_one_day.py` so it can be reused by the LLM sanity layer
(see `ingest/yazio/llm_sanity.py`) and the reprocess script
(see `ingest/yazio/reprocess_corrections.py`).

The Yazio `/user/consumed-items` endpoint returns THREE lists:
  * products[]         -- resolved by product_id (full nutrient lookup)
  * recipe_portions[]  -- resolved by recipe_id (nutrient lookup via recipe)
  * simple_products[]  -- free-form entries from AI photo / manual logging;
                          macros embedded in the item, no product_id, no
                          micronutrient detail.

We unify them into one list with two extra fields:
  * source_kind    : 'product' | 'recipe' | 'simple'
  * is_ai_estimate : True for simple_products, False otherwise.

Returns a list of dicts shaped:
    {
        "name": str,
        "amount_g": float,
        "meal": str,
        "source_kind": str,
        "is_ai_estimate": bool,
        "nutrient_alcohol_per_100g": float | None,
        "kcal_per_100g": float | None,
        "sodium_per_100g_mg": float | None,
        "fat_g_per_100g": float | None,
        "carb_g_per_100g": float | None,
        ...
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


def _num(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _sodium_g_to_mg(g: Any) -> float | None:
    v = _num(g)
    return v * 1000.0 if v is not None else None


def _chol_g_to_mg(g: Any) -> float | None:
    v = _num(g)
    return v * 1000.0 if v is not None else None


def _product_row(it: dict, products: dict) -> dict[str, Any]:
    """Resolve one products[] entry against the product lookup table."""
    pid = it.get("product_id")
    amount = it.get("amount") or 0
    meal = it.get("daytime", "?")
    p = (products or {}).get(pid) or {}
    name = p.get("name", f"<unknown {pid}>")
    nutr = p.get("nutrients", {}) or {}
    sodium = nutr.get("mineral.sodium") or nutr.get("nutrient.sodium")
    return {
        "name": name,
        "amount_g": float(amount or 0),
        "meal": meal,
        "source_kind": "product",
        "is_ai_estimate": False,
        "nutrient_alcohol_per_100g": _num(nutr.get("nutrient.alcohol")),
        "kcal_per_100g": _num(nutr.get("energy.energy")),
        "sodium_per_100g_mg": _sodium_g_to_mg(sodium),
        "fat_g_per_100g": _num(nutr.get("nutrient.fat")),
        "fat_sat_per_100g": _num(nutr.get("nutrient.saturated")),
        "carb_g_per_100g": _num(nutr.get("nutrient.carb")),
        "sugar_per_100g": _num(nutr.get("nutrient.sugar")),
        "fiber_per_100g": _num(
            nutr.get("nutrient.fiber") or nutr.get("nutrient.dietaryfiber")
        ),
        "protein_g_per_100g": _num(nutr.get("nutrient.protein")),
        "cholesterol_per_100g_mg": _chol_g_to_mg(nutr.get("nutrient.cholesterol")),
        "product_id": pid,
        "recipe_id": None,
    }


def _recipe_row(it: dict, recipes: dict) -> dict[str, Any] | None:
    """Resolve one recipe_portions[] entry.

    Yazio recipes carry per-serving (or per-portion) `nutrients` plus an
    `amount` field (mass in g for the full recipe). The consumed item gives
    a `portion_count` -- we convert everything to per-100g so the existing
    columns keep their meaning.
    """
    rid = it.get("recipe_id")
    if not rid:
        return None
    r = (recipes or {}).get(rid) or {}
    if not isinstance(r, dict) or not r:
        return None
    portion_count = _num(it.get("portion_count")) or 1.0
    meal = it.get("daytime", "?")
    # Recipe nutrients are per "serving"; mass is the total recipe weight.
    nutr = r.get("nutrients", {}) or {}
    # Some Yazio payloads expose `serving_size` / `amount` / `portions`.
    # Be defensive: if we cannot derive mass-per-portion, we still emit the
    # row with macros = NULL and let the enrichment layer fill them.
    portions_total = _num(r.get("portion_count")) or _num(r.get("portions")) or 1.0
    recipe_mass_g = _num(r.get("amount")) or _num(r.get("serving_size"))
    if recipe_mass_g and portions_total:
        portion_mass_g = recipe_mass_g / portions_total
    else:
        portion_mass_g = None
    amount_g = (portion_mass_g or 0.0) * portion_count if portion_mass_g else None
    name = r.get("name") or f"<recipe {rid}>"

    def _per_100g(key: str) -> float | None:
        v = _num(nutr.get(key))
        if v is None or portion_mass_g is None or portion_mass_g <= 0:
            return None
        return v * 100.0 / portion_mass_g

    sodium = nutr.get("mineral.sodium") or nutr.get("nutrient.sodium")
    sodium_per_100g_mg: float | None = None
    if sodium is not None and portion_mass_g and portion_mass_g > 0:
        sodium_per_100g_mg = (_num(sodium) or 0.0) * 1000.0 * 100.0 / portion_mass_g

    chol = nutr.get("nutrient.cholesterol")
    chol_per_100g_mg: float | None = None
    if chol is not None and portion_mass_g and portion_mass_g > 0:
        chol_per_100g_mg = (_num(chol) or 0.0) * 1000.0 * 100.0 / portion_mass_g

    return {
        "name": name,
        "amount_g": amount_g if amount_g is not None else 0.0,
        "meal": meal,
        "source_kind": "recipe",
        "is_ai_estimate": False,
        "nutrient_alcohol_per_100g": _per_100g("nutrient.alcohol"),
        "kcal_per_100g": _per_100g("energy.energy"),
        "sodium_per_100g_mg": sodium_per_100g_mg,
        "fat_g_per_100g": _per_100g("nutrient.fat"),
        "fat_sat_per_100g": _per_100g("nutrient.saturated"),
        "carb_g_per_100g": _per_100g("nutrient.carb"),
        "sugar_per_100g": _per_100g("nutrient.sugar"),
        "fiber_per_100g": _per_100g("nutrient.fiber")
            or _per_100g("nutrient.dietaryfiber"),
        "protein_g_per_100g": _per_100g("nutrient.protein"),
        "cholesterol_per_100g_mg": chol_per_100g_mg,
        "product_id": None,
        "recipe_id": rid,
    }


def _simple_row(it: dict) -> dict[str, Any]:
    """Project one simple_products[] entry (AI photo / freestyle).

    Yazio embeds total macros at the item amount level (e.g. 350 g serving =
    `energy`, `carb`, `protein`, `fat` already integrated). We project them
    back to per-100g to match the schema; micros (sat / sodium / sugar /
    fiber / alcohol) stay NULL -- the LLM enrichment layer fills them per-item
    using `is_ai_estimate=true`.
    """
    amount = _num(it.get("amount")) or 0.0
    meal = it.get("daytime", "?")
    name = it.get("name") or "<simple>"
    energy = _num(it.get("energy"))
    carb = _num(it.get("carb"))
    protein = _num(it.get("protein"))
    fat = _num(it.get("fat"))

    def _to_per_100g(v: float | None) -> float | None:
        if v is None or amount <= 0:
            return None
        return v * 100.0 / amount

    return {
        "name": name,
        "amount_g": amount,
        "meal": meal,
        "source_kind": "simple",
        "is_ai_estimate": True,
        "nutrient_alcohol_per_100g": None,
        "kcal_per_100g": _to_per_100g(energy),
        "sodium_per_100g_mg": None,
        "fat_g_per_100g": _to_per_100g(fat),
        "fat_sat_per_100g": None,
        "carb_g_per_100g": _to_per_100g(carb),
        "sugar_per_100g": None,
        "fiber_per_100g": None,
        "protein_g_per_100g": _to_per_100g(protein),
        "cholesterol_per_100g_mg": None,
        "product_id": None,
        "recipe_id": None,
    }


def fetch_food_items(
    d: date | str,
    token_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return the consumed food items (all 3 sources) for one date."""
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
        products_blob = json.loads(products_path.read_text())
        products = products_blob.get("products", {}) or {}
        recipes = products_blob.get("recipes", {}) or {}

    by_date = parse_food_items_from_days(days, products, recipes)
    out = by_date.get(target, [])
    n_p = sum(1 for r in out if r["source_kind"] == "product")
    n_r = sum(1 for r in out if r["source_kind"] == "recipe")
    n_s = sum(1 for r in out if r["source_kind"] == "simple")
    _log(
        f"  (fetch_food_items: {target} -> {len(out)} items "
        f"[product={n_p} recipe={n_r} simple={n_s}])"
    )
    return out


def parse_food_items_from_days(
    days: dict[str, Any],
    products: dict[str, Any],
    recipes: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Parse a multi-date `days.json` payload into food items grouped by date.

    Reads all three consumed sub-collections (products, recipe_portions,
    simple_products) and returns them in a single unified list per date,
    tagged with `source_kind` and `is_ai_estimate`.
    """
    recipes = recipes or {}
    out: dict[str, list[dict[str, Any]]] = {}
    for iso_date, day in (days or {}).items():
        consumed = (day or {}).get("consumed") or {}
        rows: list[dict[str, Any]] = []
        for it in consumed.get("products") or []:
            rows.append(_product_row(it, products or {}))
        for it in consumed.get("recipe_portions") or []:
            row = _recipe_row(it, recipes)
            if row is not None:
                rows.append(row)
        for it in consumed.get("simple_products") or []:
            rows.append(_simple_row(it))
        out[iso_date] = rows
    return out


if __name__ == "__main__":  # pragma: no cover - manual sanity
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()
    items = fetch_food_items(args.date)
    print(json.dumps(items, indent=2, ensure_ascii=False))
