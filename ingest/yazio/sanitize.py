"""
Sanitization layer for Yazio per-day nutrient values.

Yazio aggregates whatever the user typed into the food log. Free-text entries
and badly mapped database hits routinely produce physically impossible daily
totals (500 g ethanol from one beer, salt expressed in mg-as-g, saturated fat
> total fat, ...). Letting those through pollutes baselines, z-scores and
detector outputs.

This module centralises the deterministic rules. Each correction returns a
`Correction` dataclass that the caller persists in `yazio_correction` for
audit. The optional LLM sanity pass (ingest/yazio/llm_sanity.py, owned by
another agent) emits the same shape with source='llm'.

Public API:
    Correction
    apply(date_iso, kcal, fat_g, carb_g, raw_micros) -> (sanitized, corrections)

Rules implemented:
    alcohol_hard_cap          : alcohol_g > 150 g/day        -> drop
    alcohol_kcal_coherence    : 7*alcohol_g > 50% of kcal    -> drop
    sodium_hard_cap           : sodium_mg > 10 000 mg/day    -> drop
    sat_exceeds_total_fat     : fat_sat_g > fat_g * 1.05     -> drop
    sugar_exceeds_total_carb  : sugar_g > carb_g * 1.05      -> drop
    fiber_exceeds_total_carb  : fiber_g > carb_g * 1.05      -> drop
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


# ---------- canonical nutrient IDs --------------------------------------
# Mirrors the detection sets in build_daily_features.py but resolves to a
# single canonical id used in the correction log so the UI can group/filter.

NUT_ALCOHOL = "nutrient.alcohol"
NUT_SODIUM = "nutrient.sodium"
NUT_FAT_SAT = "nutrient.fat_saturated"
NUT_SUGAR = "nutrient.sugar"
NUT_FIBER = "nutrient.fiber"

# Human labels (FR) used in reason strings.
LABELS = {
    NUT_ALCOHOL: "alcool",
    NUT_SODIUM: "sodium",
    NUT_FAT_SAT: "acides gras saturés",
    NUT_SUGAR: "sucres",
    NUT_FIBER: "fibres",
}


# ---------- Correction record -------------------------------------------

@dataclass
class Correction:
    """One sanitization decision. Persisted in public.yazio_correction."""

    date: str                       # ISO YYYY-MM-DD
    nutrient_id: str                # canonical id (see NUT_* above)
    raw_value: float
    sanitized_value: float | None   # None = dropped
    source: str                     # 'rule' | 'llm'
    rule_key: str                   # e.g. 'alcohol_kcal_coherence'
    reason: str                     # FR, factual, no blame
    llm_model: str | None = None
    llm_confidence: float | None = None

    def to_row(self) -> dict:
        return {
            "date": self.date,
            "nutrient_id": self.nutrient_id,
            "raw_value": self.raw_value,
            "sanitized_value": self.sanitized_value,
            "source": self.source,
            "rule_key": self.rule_key,
            "llm_model": self.llm_model,
            "llm_confidence": self.llm_confidence,
            "reason": self.reason,
        }


# ---------- helpers -----------------------------------------------------

def _fmt(n: float, digits: int = 0) -> str:
    """Format a number FR-style: thin no-break space thousand separator."""
    if digits == 0:
        s = f"{int(round(n)):,}".replace(",", "\u202f")
    else:
        s = f"{n:,.{digits}f}".replace(",", "\u202f").replace(".", ",")
    return s


def _norm_date(d: str | date) -> str:
    if isinstance(d, date):
        return d.isoformat()
    return str(d)[:10]


# ---------- main entry point --------------------------------------------

def apply(
    d: str | date,
    kcal: float | None,
    fat_g: float | None,
    carb_g: float | None,
    raw_micros: dict[str, float],
) -> tuple[dict[str, float], list[Correction]]:
    """Apply deterministic plausibility rules.

    Parameters
    ----------
    d : ISO date string (or date) for the day being sanitized.
    kcal : Yazio daily kcal total (may be None).
    fat_g : Yazio daily fat total in grams (may be None).
    carb_g : Yazio daily carb total in grams (may be None).
    raw_micros : mapping with the already-picked canonical values, keyed by
        the NUT_* constants. Missing keys are tolerated. Values are floats.

    Returns
    -------
    (sanitized, corrections)
        sanitized : copy of raw_micros with offending entries removed.
        corrections : list of Correction records describing each drop.
    """
    date_iso = _norm_date(d)
    out = dict(raw_micros)
    corrections: list[Correction] = []

    def _drop(nid: str, raw: float, rule: str, reason: str) -> None:
        out[nid] = None  # type: ignore[assignment]
        corrections.append(
            Correction(
                date=date_iso,
                nutrient_id=nid,
                raw_value=float(raw),
                sanitized_value=None,
                source="rule",
                rule_key=rule,
                reason=reason,
            )
        )

    # --- alcohol -------------------------------------------------------
    alc = raw_micros.get(NUT_ALCOHOL)
    if alc is not None:
        if alc > 150:
            _drop(
                NUT_ALCOHOL, alc, "alcohol_hard_cap",
                f"{_fmt(alc)} g d'alcool > plafond physiologique de 150 g/j "
                "(15 verres standards).",
            )
        elif kcal is not None and kcal > 0 and (alc * 7.0) > 0.5 * kcal:
            max_g = (0.5 * kcal) / 7.0
            _drop(
                NUT_ALCOHOL, alc, "alcohol_kcal_coherence",
                f"{_fmt(alc)} g d'alcool > 50 % des kcal du jour "
                f"({_fmt(kcal)} kcal, plafond {_fmt(max_g)} g) — "
                "entrée vraisemblablement mal renseignée.",
            )

    # --- sodium --------------------------------------------------------
    na = raw_micros.get(NUT_SODIUM)
    if na is not None and na > 10000:
        _drop(
            NUT_SODIUM, na, "sodium_hard_cap",
            f"{_fmt(na)} mg de sodium > plafond de 10 000 mg/j "
            "(≈ 25 g de sel) — confusion d'unité probable.",
        )

    # --- saturated fat vs total fat -----------------------------------
    fs = raw_micros.get(NUT_FAT_SAT)
    if fs is not None and fat_g is not None and fat_g > 0 and fs > fat_g * 1.05:
        _drop(
            NUT_FAT_SAT, fs, "sat_exceeds_total_fat",
            f"{_fmt(fs, 1)} g de saturés > lipides totaux du jour "
            f"({_fmt(fat_g, 1)} g) — incohérence de saisie.",
        )

    # --- sugar vs total carb ------------------------------------------
    sg = raw_micros.get(NUT_SUGAR)
    if sg is not None and carb_g is not None and carb_g > 0 and sg > carb_g * 1.05:
        _drop(
            NUT_SUGAR, sg, "sugar_exceeds_total_carb",
            f"{_fmt(sg, 1)} g de sucres > glucides totaux du jour "
            f"({_fmt(carb_g, 1)} g) — incohérence de saisie.",
        )

    # --- fiber vs total carb ------------------------------------------
    fb = raw_micros.get(NUT_FIBER)
    if fb is not None and carb_g is not None and carb_g > 0 and fb > carb_g * 1.05:
        _drop(
            NUT_FIBER, fb, "fiber_exceeds_total_carb",
            f"{_fmt(fb, 1)} g de fibres > glucides totaux du jour "
            f"({_fmt(carb_g, 1)} g) — incohérence de saisie.",
        )

    return out, corrections


__all__ = [
    "Correction",
    "apply",
    "NUT_ALCOHOL",
    "NUT_SODIUM",
    "NUT_FAT_SAT",
    "NUT_SUGAR",
    "NUT_FIBER",
    "LABELS",
]
# Silence unused import warnings for `field` if a linter complains.
_ = field
