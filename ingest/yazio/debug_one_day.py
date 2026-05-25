"""
Debug Yazio data for a single day: identifies products that contribute the
most alcohol (or any nutrient) to the day total. Useful when the daily
summary surfaces a physically impossible value (e.g. 330 g of alcohol).

Logs everything to stderr; stdout is left clean.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def run_cli(*args: str) -> None:
    log(f"$ yazio-exporter {' '.join(args)}")
    subprocess.run(["yazio-exporter", *args], check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()
    target = args.date

    email = os.environ["YAZIO_EMAIL"]
    password = os.environ["YAZIO_PASSWORD"]

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        token = tmp / "token.txt"
        days_path = tmp / "days.json"
        products_path = tmp / "products.json"

        run_cli("login", email, password, "-o", str(token))
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

        days = json.loads(days_path.read_text())
        products = json.loads(products_path.read_text()).get("products", {})

    day = days.get(target, {})
    consumed = day.get("consumed", {}) or {}
    items = consumed.get("products", []) or []
    summary = day.get("daily_summary", {}) or {}

    rows = []
    total = {"alcohol": 0.0, "kcal": 0.0, "protein": 0.0, "carb": 0.0, "fat": 0.0}
    for it in items:
        pid = it.get("product_id")
        amount = it.get("amount") or 0
        slot = it.get("daytime", "?")
        p = products.get(pid) or {}
        name = p.get("name", f"<unknown {pid}>")
        nutr = p.get("nutrients", {}) or {}
        scale = amount / 100.0
        alc = (nutr.get("nutrient.alcohol", 0) or 0) * scale
        kcal = (nutr.get("energy.energy", 0) or 0) * scale
        prot = (nutr.get("nutrient.protein", 0) or 0) * scale
        carb = (nutr.get("nutrient.carb", 0) or 0) * scale
        fat = (nutr.get("nutrient.fat", 0) or 0) * scale
        alc_per100 = nutr.get("nutrient.alcohol", 0) or 0
        rows.append((alc, name, amount, slot, alc_per100, kcal, prot, carb, fat, pid))
        total["alcohol"] += alc
        total["kcal"] += kcal
        total["protein"] += prot
        total["carb"] += carb
        total["fat"] += fat

    rows.sort(reverse=True)

    log(f"\n=== Debug Yazio day {target} ===")
    log(f"items: {len(rows)}")
    log(f"{'alc_g':>8} {'amt_g':>7} {'alc/100g':>9} {'kcal':>7} {'slot':>10}  name")
    for alc, name, amount, slot, alc_per100, kcal, prot, carb, fat, pid in rows:
        log(f"{alc:8.2f} {amount:7.1f} {alc_per100:9.2f} {kcal:7.0f} {slot:>10}  {name}")

    log("\n--- Totals reconstructed from consumed_items ---")
    for k, v in total.items():
        log(f"  {k:>8}: {v:9.2f}")

    log("\n--- Yazio daily_summary meals ---")
    meals = summary.get("meals") if isinstance(summary, dict) else None
    if isinstance(meals, dict):
        day_alc = 0.0
        day_kcal = 0.0
        for slot, mdata in meals.items():
            nutr = (mdata or {}).get("nutrients", {}) if isinstance(mdata, dict) else {}
            alc = nutr.get("nutrient.alcohol", 0) or 0
            kcal = nutr.get("energy.energy", 0) or 0
            log(f"  {slot:>10}: alcohol={alc:7.2f} g  kcal={kcal:7.0f}")
            day_alc += alc
            day_kcal += kcal
        log(f"  {'TOTAL':>10}: alcohol={day_alc:7.2f} g  kcal={day_kcal:7.0f}")

    log("\n--- Top 3 alcohol contributors ---")
    for alc, name, amount, slot, alc_per100, kcal, prot, carb, fat, pid in rows[:3]:
        log(f"  {name!r}  amount={amount}g  alc/100g={alc_per100}  -> {alc:.2f} g alcohol  (pid={pid})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
