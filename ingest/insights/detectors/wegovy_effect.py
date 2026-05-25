"""Famille 5 — effet anorexigène Wegovy mesuré.

Compare kcal moyen post-Wegovy (depuis start_date 2026-04-14) vs baseline
180 jours pré-Wegovy. Si l'écart dépasse 200 kcal/j en baisse, on flag.
Calcule en bonus le ratio J-7 vs J-21 (proxy plateau pharmaco).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..scoring import InsightCandidate, iso_week_bucket, recency_from_last_date

WEGOVY_START = date(2026, 4, 14)
MIN_DELTA_KCAL = 200.0


def detect(df: pd.DataFrame, today: date) -> list[InsightCandidate]:
    if df.empty or "kcal" not in df.columns:
        return []
    if today < WEGOVY_START:
        return []

    df = df.copy()
    df["date_d"] = pd.to_datetime(df["date"]).dt.date

    post = df[df["date_d"] >= WEGOVY_START]
    post = post.dropna(subset=["kcal"])
    post = post[post["kcal"] > 0]
    if len(post) < 7:
        return []

    pre_start = pd.Timestamp(WEGOVY_START) - pd.Timedelta(days=180)
    pre = df[(df["date_d"] >= pre_start.date()) & (df["date_d"] < WEGOVY_START)]
    pre = pre.dropna(subset=["kcal"])
    pre = pre[pre["kcal"] > 0]
    if len(pre) < 30:
        return []

    pre_mean = float(pre["kcal"].mean())
    post_mean = float(post["kcal"].mean())
    delta = post_mean - pre_mean  # negative = anorexigène

    if delta > -MIN_DELTA_KCAL:
        return []

    # plateau proxy: last 7 days vs 14-21 days ago
    last_7 = post.sort_values("date_d").tail(7)
    prev_window = post.sort_values("date_d").iloc[-21:-7] if len(post) >= 21 else None
    plateau_note = ""
    plateau_data: dict[str, float] = {}
    if prev_window is not None and len(prev_window) >= 5 and len(last_7) >= 5:
        last_7_mean = float(last_7["kcal"].mean())
        prev_mean = float(prev_window["kcal"].mean())
        plateau_data = {
            "last_7d_mean_kcal": round(last_7_mean, 0),
            "j14_j21_mean_kcal": round(prev_mean, 0),
        }
        diff = last_7_mean - prev_mean
        if abs(diff) < 100:
            plateau_note = " J-7 vs J14-J21 quasi stable: l'effet semble plateauer."
        elif diff < -100:
            plateau_note = f" J-7 encore en baisse ({diff:+.0f} kcal/j vs J14-J21)."
        else:
            plateau_note = f" J-7 en remontée ({diff:+.0f} kcal/j vs J14-J21)."

    last_d = post["date_d"].max()
    severity = "info" if abs(delta) < 400 else "watch"
    magnitude = min(15.0, abs(delta) / 50.0)

    title = f"Effet Wegovy : {delta:+.0f} kcal/j"
    body = f"Apport {post_mean:.0f} kcal/j vs baseline {pre_mean:.0f}.{plateau_note}"

    return [
        InsightCandidate(
            detector_key="wegovy_effect",
            family=5,
            severity=severity,
            title=title,
            body=body,
            bucket=iso_week_bucket(last_d),
            data={
                "pre_mean_kcal": round(pre_mean, 0),
                "post_mean_kcal": round(post_mean, 0),
                "delta_kcal": round(delta, 0),
                "n_post_days": len(post),
                "n_pre_days": len(pre),
                "wegovy_start": WEGOVY_START.isoformat(),
                **plateau_data,
            },
            metric_keys=["kcal"],
            link_href="/detail/wegovy",
            magnitude_bonus=magnitude,
            recency_bonus=recency_from_last_date(last_d, today),
        )
    ]
