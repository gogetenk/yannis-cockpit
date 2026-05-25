"""Famille 1 — budget alcool hebdomadaire.

Cible OMS faible risque: 14g/j (≈ 98g/sem). Si la somme rolling 7j dépasse
98g, on emet un candidat. Si on a une mesure SBP sur la même fenêtre, on
cite le lien dose-dépendant (Di Federico 2023 méta-analyse, +1.25 mmHg
SBP par tranche de 12g/j).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..scoring import InsightCandidate, iso_week_bucket, recency_from_last_date

DAILY_TARGET_G = 14.0
WEEKLY_THRESHOLD_G = DAILY_TARGET_G * 7  # 98g


def detect(df: pd.DataFrame, today: date) -> list[InsightCandidate]:
    if df.empty or "alcohol_g" not in df.columns:
        return []
    window = df.sort_values("date").tail(7).copy()
    window["alcohol_g"] = window["alcohol_g"].fillna(0)
    if len(window) < 5:
        return []

    total = float(window["alcohol_g"].sum())
    if total <= WEEKLY_THRESHOLD_G:
        return []

    severity = "alert" if total > 150 else "watch"
    magnitude = min(15.0, max(0.0, (total - WEEKLY_THRESHOLD_G) / 10.0))
    last_d = pd.to_datetime(window["date"].max()).date()

    sbp_clause = ""
    if "sbp" in window.columns:
        sbp_vals = window["sbp"].dropna()
        if not sbp_vals.empty:
            sbp_mean = float(sbp_vals.mean())
            sbp_clause = f" SBP moy {sbp_mean:.0f} mmHg sur la fenêtre."

    title = f"Alcool 7j : {total:.0f} g"
    body = f"Au-dessus du seuil OMS (98 g/sem).{sbp_clause}"

    return [
        InsightCandidate(
            detector_key="budget_alcohol",
            family=1,
            severity=severity,
            title=title,
            body=body,
            bucket=iso_week_bucket(last_d),
            data={
                "window_days": 7,
                "total_g": round(total, 1),
                "threshold_g": WEEKLY_THRESHOLD_G,
                "last_date": last_d.isoformat(),
            },
            metric_keys=["alcohol_g", "sbp"],
            link_href="/detail/cardio",
            magnitude_bonus=magnitude,
            recency_bonus=recency_from_last_date(last_d, today),
        )
    ]
