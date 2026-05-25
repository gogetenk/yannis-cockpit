"""LLM second-opinion layer for Yazio nutrient corrections (Haiku 4.5).

This module is a *standalone* arbitration helper. It is NOT auto-invoked by
the deterministic sanitizer (`ingest/yazio/sanitize.py`). The intended
integration, in a future PR, is for `sanitize.apply` (or its caller in
`build_daily_features.py`) to escalate borderline rule hits to
`review_correction` — typically when the rule fired near a threshold:

    * alcohol_kcal_coherence: ratio alcohol_kcal / daily_kcal in [0.45, 0.55]
    * sodium: raw value in [8000, 12000] mg/day
    * saturated/sugar/fiber slightly above their macro parent

The LLM may either confirm the rule (drop / refine) or veto it (keep the
raw value, log the disagreement). It is NEVER allowed to invent a value
ex nihilo: any `refined_value` it proposes is clamped against a hard-coded
physiological whitelist, and on any failure (timeout, malformed JSON, API
error, out-of-range value) we silently fall back to the deterministic
correction so the ingest pipeline never blocks on a third-party outage.

Model: claude-haiku-4-5-20251001
Pricing (informational, do not auto-budget here):
    Haiku 4.5: ~$0.001/M input tok, ~$0.005/M output tok.
    ~50 reviews/day x ~200 tok in + ~80 tok out -> sub-cent per day.

Requires the `anthropic` SDK (already in ingest/snapshot/requirements.txt).
ANTHROPIC_API_KEY must be set in the environment; if absent the function
returns the original deterministic correction unchanged.
"""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ingest.yazio.sanitize import Correction

MODEL = "claude-haiku-4-5-20251001"

# Hard physiological caps. The LLM-proposed `refined_value` MUST land in
# the closed interval for the given nutrient or the LLM correction is
# discarded and we fall back to the deterministic rule.
#
# For macros that should be bounded by their parent macro (saturated <= fat,
# sugar <= carbs, fiber <= carbs), the upper bound is computed dynamically
# from `food_items` if available, else from a generous static fallback.
STATIC_RANGES: dict[str, tuple[float, float]] = {
    "alcohol": (0.0, 150.0),       # g/day
    "sodium": (0.0, 10_000.0),     # mg/day
    "saturated": (0.0, 300.0),     # g/day fallback (otherwise fat_total)
    "sugar": (0.0, 800.0),         # g/day fallback (otherwise carbs_total)
    "fiber": (0.0, 200.0),         # g/day fallback (otherwise carbs_total)
}


def _canonicalize_nid(nutrient_id: str) -> str:
    """Map a canonical Yazio nutrient_id (`nutrient.alcohol`, `nutrient.fat_saturated`, ...)
    to a STATIC_RANGES / parent-macro key (`alcohol`, `saturated`, ...).
    Tolerates bare keys for tests."""
    nid = (nutrient_id or "").lower()
    if "fat_saturated" in nid or "saturated" in nid:
        return "saturated"
    if "alcohol" in nid:
        return "alcohol"
    if "sodium" in nid:
        return "sodium"
    if "sugar" in nid:
        return "sugar"
    if "fiber" in nid or "fibre" in nid:
        return "fiber"
    return nid


def _parent_macro_cap(nutrient_id: str, food_items: list[dict] | None) -> float | None:
    """Return a dynamic upper bound from the day's food items, or None."""
    if not food_items:
        return None
    key = _canonicalize_nid(nutrient_id)
    try:
        if key == "saturated":
            total = sum(float(it.get("fat_g") or 0) for it in food_items)
            return total if total > 0 else None
        if key in ("sugar", "fiber"):
            total = sum(float(it.get("carb_g") or 0) for it in food_items)
            return total if total > 0 else None
    except (TypeError, ValueError):
        return None
    return None


def _range_for(nutrient_id: str, food_items: list[dict] | None) -> tuple[float, float] | None:
    """Resolve the physiological [lo, hi] range for a nutrient."""
    base = STATIC_RANGES.get(_canonicalize_nid(nutrient_id))
    if base is None:
        return None
    lo, hi = base
    dyn_cap = _parent_macro_cap(nutrient_id, food_items)
    if dyn_cap is not None:
        # Use the tighter of (static fallback, dynamic parent cap).
        hi = min(hi, dyn_cap)
    return (lo, hi)


def _build_user_payload(
    correction: "Correction",
    food_items: list[dict] | None,
    daily_kcal: float | None,
) -> dict[str, Any]:
    """Compact JSON payload sent to the LLM as the user message."""
    # Project food_items to the minimum needed for arbitration to keep
    # token cost flat.
    slim_items: list[dict] = []
    if food_items:
        for it in food_items[:40]:  # hard cap to avoid runaway prompts
            slim_items.append({
                "name": it.get("name"),
                "amount_g": it.get("amount_g"),
                "meal": it.get("meal"),
                "alcohol_per_100g": (
                    it.get("nutrient_alcohol_per_100g")
                    if it.get("nutrient_alcohol_per_100g") is not None
                    else it.get("alcohol_g_per_100g")
                ),
                "kcal_per_100g": it.get("kcal_per_100g"),
                "sodium_per_100g_mg": it.get("sodium_per_100g_mg"),
            })
    return {
        "nutrient": correction.nutrient_id,
        "raw_value": correction.raw_value,
        "rule_proposed_value": correction.sanitized_value,
        "rule_key": correction.rule_key,
        "rule_reason": correction.reason,
        "date": str(correction.date),
        "daily_kcal": daily_kcal,
        "food_items": slim_items,
    }


SYSTEM_PROMPT = (
    "Tu es un arbitre nutritionnel. Une règle déterministe a flaggé une "
    "valeur Yazio comme implausible. Tu dois soit confirmer la règle "
    "(plausible=false, refined_value=null = drop), soit proposer une valeur "
    "affinée basée UNIQUEMENT sur les food_items fournis (plausible=false, "
    "refined_value=<g_ou_mg>), soit dire que la valeur brute est en fait "
    "correcte (plausible=true).\n\n"
    "Tu n'inventes JAMAIS une valeur sans support dans les items.\n\n"
    "ALCOOL — si un item est une boisson alcoolisée avec amount_g et "
    "nutrient.alcohol/100g aberrant (souvent 0 g/g alors que la boisson est "
    "alcoolisée), recalcule la valeur typique (ABV g d'éthanol par g de boisson) :\n"
    "  - bière (IPA, lager, blonde, ale, stout, porter) : 0.04 à 0.07 g/g\n"
    "  - vin (rouge, blanc, rosé, champagne, prosecco) : 0.10 à 0.14 g/g\n"
    "  - spiritueux (vodka, whisky, rhum, gin, tequila, cognac) : 0.30 à 0.40 g/g\n"
    "  - cocktail / spritz / aperol / mojito / sangria : 0.05 à 0.15 g/g selon dilution\n"
    "  - liqueur, porto, sherry : 0.15 à 0.25 g/g\n"
    "  - cidre, kombucha alcoolisé : 0.03 à 0.05 g/g\n"
    "  Somme sur TOUS les items alcoolisés du jour pour obtenir l'alcool total.\n"
    "  Cas spécial rule_key='alcohol_false_zero' : raw_value=0 mais des items "
    "  sont en fait alcoolisés. Réponds plausible=false, refined_value=<somme "
    "  estimée en g d'éthanol>. Borne finale [0, 150 g/j].\n\n"
    "SODIUM — juge la cohérence avec les aliments. Charcuterie, sushi, "
    "ramen, restau asiatique peuvent légitimement dépasser 5 000 mg/j. "
    "Au-dessus de 10 000 mg c'est presque toujours une confusion d'unité.\n\n"
    "MACROS (saturés/sucres/fibres) — ne peuvent jamais dépasser leur "
    "macro parent (fat_g pour les saturés, carb_g pour sucres/fibres).\n\n"
    "Si tu n'es pas confiant, confirme la règle (refined_value=null). "
    "Réponds via l'outil JSON."
)

TOOL_SCHEMA = {
    "name": "arbitrate",
    "description": "Render the arbitration verdict.",
    "input_schema": {
        "type": "object",
        "properties": {
            "plausible": {
                "type": "boolean",
                "description": "True if raw_value is actually plausible (rule is wrong).",
            },
            "refined_value": {
                "type": ["number", "null"],
                "description": "Refined value if rule is right but sanitized_value can be improved. Null otherwise.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
            "reason_fr": {
                "type": "string",
                "maxLength": 100,
            },
        },
        "required": ["plausible", "refined_value", "confidence", "reason_fr"],
    },
}


def _call_llm(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Call Haiku 4.5 once, no retry. Returns parsed tool input or None."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("  (llm_sanity: skipped, ANTHROPIC_API_KEY not set)", file=sys.stderr)
        return None
    try:
        import anthropic
    except ImportError:
        print("  (llm_sanity: skipped, anthropic SDK not installed)", file=sys.stderr)
        return None

    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=400,
            system=SYSTEM_PROMPT,
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "arbitrate"},
            messages=[{
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, default=str),
            }],
        )
        for block in resp.content or []:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "arbitrate":
                raw = block.input
                if isinstance(raw, dict):
                    return raw
                if isinstance(raw, str):
                    return json.loads(raw)
        print("  (llm_sanity: no tool_use block in response)", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  (llm_sanity: LLM call failed: {e})", file=sys.stderr)
        return None


def review_correction(
    correction: "Correction",
    food_items: list[dict] | None,
    daily_kcal: float | None,
) -> "Correction":
    """Return a (possibly amended) Correction.

    The deterministic rule has already flagged `correction.raw_value` as
    implausible and proposed `correction.sanitized_value` (often None = drop).
    The LLM is asked to confirm OR propose a refined value, NEVER to invent
    a measurement from scratch. If the LLM disagrees with the rule, returns
    a correction with source='llm' and the proposed sanitized_value bounded
    by physiological ranges.
    """
    # Late import: Correction lives in sanitize.py owned by the parallel agent.
    from dataclasses import replace

    from ingest.yazio.sanitize import Correction  # noqa: F401  (runtime import)

    payload = _build_user_payload(correction, food_items, daily_kcal)
    verdict = _call_llm(payload)

    if verdict is None:
        # API/SDK/parse failure -> keep deterministic correction as-is.
        return correction

    try:
        plausible = bool(verdict["plausible"])
        refined_value = verdict.get("refined_value")
        confidence = float(verdict.get("confidence") or 0.0)
        reason_fr = str(verdict.get("reason_fr") or "")[:100]
    except (KeyError, TypeError, ValueError) as e:
        print(f"  (llm_sanity: malformed verdict, falling back: {e})", file=sys.stderr)
        return correction

    confidence = max(0.0, min(1.0, confidence))

    print(
        f"  (llm_sanity: {correction.nutrient_id} date={correction.date} "
        f"model={MODEL} plausible={plausible} confidence={confidence:.2f})",
        file=sys.stderr,
    )

    rng = _range_for(correction.nutrient_id, food_items)

    if plausible:
        # The LLM dements the rule: keep raw_value, but flag the disagreement
        # via source='llm' and the supplied reason.
        return replace(
            correction,
            sanitized_value=correction.raw_value,
            source="llm",
            reason=f"LLM veto rule {correction.rule_key}: {reason_fr}",
            llm_model=MODEL,
            llm_confidence=confidence,
        )

    # Rule was right (plausible=false). Either accept its drop, or accept
    # a refined value if the LLM proposed one AND it sits in the whitelist.
    if refined_value is None:
        # No refinement -> stick with the deterministic correction but
        # annotate it as LLM-confirmed.
        return replace(
            correction,
            source="llm",
            reason=f"LLM confirme {correction.rule_key}: {reason_fr}",
            llm_model=MODEL,
            llm_confidence=confidence,
        )

    try:
        rv = float(refined_value)
    except (TypeError, ValueError):
        print(
            f"  (llm_sanity: non-numeric refined_value={refined_value!r}, "
            f"falling back to rule)",
            file=sys.stderr,
        )
        return correction

    if rng is not None:
        lo, hi = rng
        if not (lo <= rv <= hi):
            print(
                f"  (llm_sanity: refined_value={rv} out of physiological "
                f"range [{lo},{hi}] for {correction.nutrient_id}, "
                f"falling back to rule)",
                file=sys.stderr,
            )
            return correction
    # If we have no whitelist for this nutrient, conservatively refuse the
    # refinement and fall back on the deterministic rule.
    else:
        print(
            f"  (llm_sanity: no whitelist for {correction.nutrient_id}, "
            f"falling back to rule)",
            file=sys.stderr,
        )
        return correction

    return replace(
        correction,
        sanitized_value=rv,
        source="llm",
        reason=f"LLM refine {correction.rule_key}: {reason_fr}",
        llm_model=MODEL,
        llm_confidence=confidence,
    )
