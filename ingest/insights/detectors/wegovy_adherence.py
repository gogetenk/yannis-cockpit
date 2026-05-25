"""Famille 5 — adhérence injections Wegovy (rythme hebdo).

Lit wegovy_injection sur les 8 dernières semaines. Compte les intervalles
> 8 jours entre 2 injections consécutives (retard ≥ 24h sur le rythme).
≥1 retard → watch, ≥2 → alert.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..scoring import InsightCandidate, recency_from_last_date

LOOKBACK_DAYS = 56  # 8 semaines


def detect(today: date, sb_client: Any) -> list[InsightCandidate]:
    since = today - timedelta(days=LOOKBACK_DAYS)
    try:
        rows = sb_client.request(
            "GET",
            "wegovy_injection",
            params={
                "select": "date,dose_mg",
                "date": f"gte.{since.isoformat()}",
                "order": "date.asc",
            },
        )
    except Exception:
        return []
    if not rows or len(rows) < 2:
        return []

    dates = [date.fromisoformat(r["date"]) for r in rows]
    gaps: list[tuple[date, date, int]] = []
    for i in range(1, len(dates)):
        gap = (dates[i] - dates[i - 1]).days
        if gap > 8:
            gaps.append((dates[i - 1], dates[i], gap))

    last_inj = dates[-1]
    today_gap = (today - last_inj).days
    overdue_now = today_gap > 8  # missed this week's injection

    n_late = len(gaps) + (1 if overdue_now else 0)
    if n_late == 0:
        return []

    severity = "alert" if n_late >= 2 else "watch"
    magnitude = min(15.0, n_late * 5.0)

    parts = []
    if gaps:
        examples = ", ".join(
            f"{a.isoformat()}→{b.isoformat()} ({g}j)" for a, b, g in gaps[-3:]
        )
        parts.append(f"{len(gaps)} intervalle(s) > 8j sur 8 semaines ({examples})")
    if overdue_now:
        parts.append(
            f"dernière injection {last_inj.isoformat()} = {today_gap}j (rythme hebdo)"
        )

    title = f"Adhérence Wegovy: {n_late} décalage(s) détecté(s)"
    body = (
        "Rythme cible: 1 injection / 7j. " + " ; ".join(parts) +
        ". Les décalages réduisent l'exposition moyenne au sémaglutide et "
        "peuvent atténuer l'effet anorexigène la semaine suivante."
    )

    return [
        InsightCandidate(
            detector_key="wegovy_adherence",
            family=5,
            severity=severity,
            title=title,
            body=body,
            bucket=f"asof-{today.isoformat()}",
            data={
                "lookback_days": LOOKBACK_DAYS,
                "n_late_gaps": len(gaps),
                "overdue_now": overdue_now,
                "last_injection": last_inj.isoformat(),
                "days_since_last": today_gap,
                "late_gaps": [
                    {"prev": a.isoformat(), "next": b.isoformat(), "gap_days": g}
                    for a, b, g in gaps
                ],
            },
            metric_keys=["wegovy_dose_mg"],
            link_href="/detail/wegovy",
            magnitude_bonus=magnitude,
            recency_bonus=recency_from_last_date(last_inj, today),
        )
    ]
