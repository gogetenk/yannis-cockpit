"""Shared types + scoring helpers for insight detectors.

Each detector returns a list of `InsightCandidate`. The orchestrator
(`detect.py`) computes the final score, hash_dedup and UPSERTs into
`insight`, then marks any insight whose hash_dedup wasn't re-detected
this run as `active = false` (auto-expire).

Score model: severity_weight + magnitude_bonus + recency_bonus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

SEVERITY_WEIGHT = {"alert": 80, "watch": 50, "info": 20}


@dataclass
class InsightCandidate:
    detector_key: str
    family: int
    severity: str  # 'info' | 'watch' | 'alert'
    title: str
    body: str
    bucket: str  # discrete bucket → hash_dedup = f"{detector_key}|{bucket}"
    data: dict[str, Any] = field(default_factory=dict)
    metric_keys: list[str] = field(default_factory=list)
    link_href: str | None = None
    # 0..15, how strong the signal is inside its severity band
    magnitude_bonus: float = 0.0
    # 0..5, freshness of the underlying data (today=5, fades linearly)
    recency_bonus: float = 5.0

    def score(self) -> float:
        base = SEVERITY_WEIGHT.get(self.severity, 0)
        return float(min(100.0, base + self.magnitude_bonus + self.recency_bonus))

    def hash_dedup(self) -> str:
        return f"{self.detector_key}|{self.bucket}"


def recency_from_last_date(last_d: date | None, today: date) -> float:
    """Linear decay: same day = 5, 7+ days old = 0."""
    if last_d is None:
        return 0.0
    delta = (today - last_d).days
    if delta <= 0:
        return 5.0
    if delta >= 7:
        return 0.0
    return round(5.0 * (1 - delta / 7.0), 2)


def iso_week_bucket(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"
