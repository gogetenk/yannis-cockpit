"""Famille 1 — budget acides gras saturés.

Cible: AHA recommande <=6% E. Limite haute de tolérance retenue ici: 10%E
(au-delà, l'effet LDL est documenté — Mensink-Katan). Sur les 14 derniers
jours, on flag si la médiane pct_e_sat > 10 ET ≥10 jours dépassent 10%.

Registre exploratoire: on cite la magnitude + la fenêtre, jamais "élimine".
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..scoring import (
    SEVERITY_DOWNSCOPE,
    InsightCandidate,
    adjusted_measured_ratio,
    iso_week_bucket,
    recency_from_last_date,
)


def detect(df: pd.DataFrame, today: date) -> list[InsightCandidate]:
    if df.empty or "pct_e_sat" not in df.columns:
        return []
    window = df.sort_values("date").tail(14).copy()
    # Only consider logged days -- pct_e_sat derived from kcal=0 makes no sense.
    if "is_logged" in window.columns:
        window = window[window["is_logged"] == True]  # noqa: E712
    window = window.dropna(subset=["pct_e_sat"])
    if len(window) < 7:
        return []
    # < 50% logged coverage on the 14d window = not enough signal to fire.
    if len(window) / 14.0 < 0.5:
        return []

    n_high = int((window["pct_e_sat"] > 10).sum())
    median = float(window["pct_e_sat"].median())
    if n_high < 7 or median <= 10:
        return []

    coverage = adjusted_measured_ratio(window, "fat_sat_g_source")
    # Not enough measured signal to justify firing.
    if coverage < 0.2:
        return []

    severity = "alert" if median > 15 else "watch"
    if coverage < 0.5:
        severity = SEVERITY_DOWNSCOPE.get(severity, severity)
    magnitude = min(15.0, max(0.0, (median - 10.0) * 2.0))
    last_d = pd.to_datetime(window["date"].max()).date()

    title = f"Saturés : {median:.0f} %E sur 14 j"
    body = f"{n_high}/14 jours au-dessus de 10 %E (cible AHA ≤ 6)."
    if coverage < 0.7:
        body += " (estimé en partie depuis macros — précision limitée)"

    return [
        InsightCandidate(
            detector_key="budget_saturated_fat",
            family=1,
            severity=severity,
            title=title,
            body=body,
            bucket=iso_week_bucket(last_d),
            data={
                "window_days": 14,
                "n_high_days": n_high,
                "median_pct_e_sat": round(median, 2),
                "threshold_pct_e": 10.0,
                "last_date": last_d.isoformat(),
                "coverage": round(coverage, 3),
            },
            metric_keys=["fat_sat_g", "pct_e_sat"],
            link_href="/detail/biology",
            magnitude_bonus=magnitude,
            recency_bonus=recency_from_last_date(last_d, today),
        )
    ]
