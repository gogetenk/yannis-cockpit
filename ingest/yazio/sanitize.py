"""
Sanitization layer for Yazio per-day nutrient values.

False-zero alcohol detection
----------------------------
`detect_alcohol_false_zero(date, food_items)` inspects raw food_items (not
totals) to catch the case where the user logged a beer/wine/spirit but the
matched Yazio product carries 0 g/g alcohol. The deterministic step emits
a Correction with sanitized_value=None — the LLM is expected to estimate
the actual ethanol mass from the matched items.

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

import re
import unicodedata
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


# ---------- false-zero alcohol detection -------------------------------

# Whole-word, accent-stripped, lower-case matching on item names.
ALCOHOL_NAME_PATTERN = re.compile(
    r"\b("
    r"bier|biere|beer|"
    r"vin|wine|rouge|blanc|rose|"
    r"vodka|whisky|whiskey|rhum|rum|gin|tequila|"
    r"champagne|prosecco|aperol|spritz|cocktail|mojito|"
    r"ricard|pastis|pastaga|"
    r"ipa|lager|ale|stout|porter|"
    r"kir|sangria|martini|negroni|"
    r"bourbon|cognac|armagnac|calvados|"
    r"liqueur|porto|sherry|hugo|"
    r"moscow|bloody|caipi|caipirinha"
    r")\b"
)

# Minimum gram amount for a single item to be considered a real drink (filter
# out trace ingredients like "vin blanc 5g pour la sauce").
FALSE_ZERO_MIN_AMOUNT_G = 50.0

# Per-100g alcohol ceiling under which an item is considered "reported as zero".
FALSE_ZERO_ALCOHOL_CEILING = 0.01  # g per g


def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _name_matches_alcohol(name: str | None) -> bool:
    if not name:
        return False
    norm = _strip_accents(name).lower()
    return bool(ALCOHOL_NAME_PATTERN.search(norm))


def detect_alcohol_false_zero(
    d: str | date,
    food_items: list[dict] | None,
) -> Correction | None:
    """Inspect food_items for boissons alcoolisées loggées avec 0 g d'alcool.

    Returns a single Correction(rule_key='alcohol_false_zero',
    sanitized_value=None) if at least one item matches the regex AND carries
    amount_g > 50 AND nutrient.alcohol per_100g <= 0.01. The LLM is then
    expected to estimate the day's total ethanol from the matched items.

    Returns None if no item matches.
    """
    if not food_items:
        return None
    matches: list[dict] = []
    for it in food_items:
        name = it.get("name")
        if not _name_matches_alcohol(name):
            continue
        try:
            amount = float(it.get("amount_g") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        if amount <= FALSE_ZERO_MIN_AMOUNT_G:
            continue
        alc_per_100g = it.get("nutrient_alcohol_per_100g")
        try:
            alc = float(alc_per_100g) if alc_per_100g is not None else 0.0
        except (TypeError, ValueError):
            alc = 0.0
        if alc <= FALSE_ZERO_ALCOHOL_CEILING:
            matches.append(it)

    if not matches:
        return None

    date_iso = _norm_date(d)
    names = ", ".join(
        f"{m.get('name', '?')} ({int(float(m.get('amount_g') or 0))} g)"
        for m in matches[:4]
    )
    if len(matches) > 4:
        names += f", +{len(matches) - 4} autres"
    reason = (
        f"Bière/vin/spiritueux loggé sans alcool reporté "
        f"({len(matches)} item{'s' if len(matches) > 1 else ''} concerné"
        f"{'s' if len(matches) > 1 else ''} : {names})."
    )
    return Correction(
        date=date_iso,
        nutrient_id=NUT_ALCOHOL,
        raw_value=0.0,
        sanitized_value=None,  # LLM decides
        source="rule",
        rule_key="alcohol_false_zero",
        reason=reason,
    )


__all__ = [
    "Correction",
    "apply",
    "detect_alcohol_false_zero",
    "NUT_ALCOHOL",
    "NUT_SODIUM",
    "NUT_FAT_SAT",
    "NUT_SUGAR",
    "NUT_FIBER",
    "LABELS",
    "ALCOHOL_NAME_PATTERN",
]
# Silence unused import warnings for `field` if a linter complains.
_ = field
