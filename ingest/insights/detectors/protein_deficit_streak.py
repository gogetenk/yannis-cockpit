"""Famille 1 — streak de déficit protéique.

Cible: 1.6 g/kg/j (perte de poids sous GLP-1, prévention sarcopénie).
On flag si ≥4 jours consécutifs (sur les 21 derniers) à <80% de la cible.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..scoring import InsightCandidate, iso_week_bucket, recency_from_last_date

TARGET_G_PER_KG = 1.6
DEFICIT_RATIO = 0.8
WINDOW_DAYS = 21
MIN_STREAK = 4


def detect(df: pd.DataFrame, today: date) -> list[InsightCandidate]:
    if df.empty or "protein_g" not in df.columns or "weight_kg" not in df.columns:
        return []

    window = df.sort_values("date").tail(WINDOW_DAYS).copy()
    # Fill weight_kg forward then backward so we always have a reference.
    window["weight_kg"] = window["weight_kg"].ffill().bfill()
    window = window.dropna(subset=["protein_g", "weight_kg"])
    # Drop days where protein_g == 0: almost certainly "Yazio non loggé" rather
    # than réel déficit. Évite des faux positifs sur les jours sans saisie.
    window = window[window["protein_g"] > 0]
    if len(window) < MIN_STREAK:
        return []

    window["target_g"] = window["weight_kg"] * TARGET_G_PER_KG
    window["deficit"] = window["protein_g"] < (DEFICIT_RATIO * window["target_g"])

    # Find longest trailing streak (must end at the most recent row).
    deficits = window["deficit"].tolist()
    streak = 0
    for v in reversed(deficits):
        if v:
            streak += 1
        else:
            break

    if streak < MIN_STREAK:
        return []

    last_d = pd.to_datetime(window["date"].max()).date()
    mean_protein = float(window.tail(streak)["protein_g"].mean())
    mean_target = float(window.tail(streak)["target_g"].mean())
    pct = mean_protein / mean_target * 100 if mean_target else 0.0

    severity = "alert" if streak >= 7 else "watch"
    magnitude = min(15.0, float(streak))

    title = f"Protéines : {streak} j sous cible"
    body = f"{mean_protein:.0f} g/j vs cible {mean_target:.0f} g (1,6 g/kg)."

    return [
        InsightCandidate(
            detector_key="protein_deficit_streak",
            family=1,
            severity=severity,
            title=title,
            body=body,
            bucket=iso_week_bucket(last_d),
            data={
                "streak_days": streak,
                "mean_protein_g": round(mean_protein, 1),
                "mean_target_g": round(mean_target, 1),
                "pct_of_target": round(pct, 1),
                "last_date": last_d.isoformat(),
            },
            metric_keys=["protein_g", "weight_kg"],
            link_href="/detail/composition",
            magnitude_bonus=magnitude,
            recency_bonus=recency_from_last_date(last_d, today),
        )
    ]
