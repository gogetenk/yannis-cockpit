"""LLM micronutrient estimation for Yazio days that only carry macros+kcal.

When the user logs meals via Yazio's photo-AI flow, only kcal+macros come
back -- saturated fat, sodium, sugar, fiber, alcohol are simply absent from
`yazio_micronutrient_daily`. That hides the user's primary LDL lever
(saturated fat, Mensink-Katan) and breaks sodium / fibre tracking.

This module asks Haiku 4.5 to *estimate* the missing micronutrients from the
day's meal context (names + per-meal kcal/macros), under tight physiological
clamps. Estimates are flagged `source='llm_estimate'` in daily_features so
the UI can show them differently from Yazio-measured values, and so the LDL
detector can lower its confidence when the input is estimated.

The estimator is deliberately conservative:
  * Returns null for nutrients it cannot anchor in the day's items
  * Clamps every value to STATIC_RANGES (matches llm_sanity)
  * NEVER overrides an existing Yazio value -- only fills `existing_micros[k] is None`
  * Fails open: any API/SDK error returns {} so the pipeline never blocks

Model: claude-haiku-4-5-20251001
Cost: ~$0.40 for a 730-day backrun (~50-100 tok in + ~80 tok out per day)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

MODEL = "claude-haiku-4-5-20251001"

# Mirrors STATIC_RANGES in llm_sanity. Saturated/sugar/fiber upper bounds are
# additionally narrowed dynamically by their parent macro at clamp time.
STATIC_RANGES: dict[str, tuple[float, float]] = {
    "fat_sat_g": (0.0, 300.0),
    "sodium_mg": (0.0, 6000.0),
    "sugar_g": (0.0, 800.0),
    "fiber_g": (0.0, 200.0),
    "alcohol_g": (0.0, 150.0),
}

# Order matters: tool schema and prompt reference these keys.
NUTRIENT_KEYS = ("fat_sat_g", "sodium_mg", "sugar_g", "fiber_g", "alcohol_g")


SYSTEM_PROMPT = (
    "Tu es un nutritionniste qui ESTIME les micronutriments manquants d'une "
    "journee alimentaire a partir d'une liste de repas (nom + kcal + macros). "
    "Tu ne DOIS PAS inventer une estimation quand les repas ne sont pas "
    "decrits assez precisement (ex: 'dejeuner' sans details) -> retourne null "
    "pour ce nutriment avec reason='items insuffisamment decrits'.\n\n"
    "REGLES DE BOUCHAGE (utilise les categories pour ancrer l'estimation):\n"
    "- Restaurant asiatique / sushi / ramen / wok : sodium 1500-3000 mg/plat principal\n"
    "- Burger / fast-food (McDo, BK, KFC) : SFA ~25-35% du fat_g du repas, "
    "sodium 800-1500 mg/burger\n"
    "- Pizza : sodium 600-1200 mg/part, SFA 25-30% du fat (fromage)\n"
    "- Charcuterie / fromage affine / beurre / creme : SFA 60-70% du fat\n"
    "- Salade composee maison : sodium 200-600 mg, SFA 15-25% du fat\n"
    "- Plat prepare industriel / surgele : sodium typiquement plus eleve "
    "qu'une estimation naive (>= 600 mg/portion)\n"
    "- Petit-dejeuner classique (cafe, pain, beurre, confiture) : sodium 200-500 mg, "
    "SFA depend du beurre (60-70% du fat)\n"
    "- Plats vagues type 'salade', 'burger maison', 'petit-dej equilibre' : "
    "estimer selon la categorie sans inventer de chiffres precis\n\n"
    "BORNES PHYSIOLOGIQUES STRICTES:\n"
    "- fat_sat_g <= fat_g_total du jour (et clampe a 0.95 * fat_g_total max)\n"
    "- sodium_mg dans [0, 6000] (au-dessus = improbable sans contexte extreme)\n"
    "- sugar_g <= carb_g_total du jour\n"
    "- fiber_g <= 0.4 * carb_g_total\n"
    "- alcohol_g dans [0, 150] (seulement si une boisson alcoolisee est nommee)\n\n"
    "Si EXISTING_MICROS contient deja une valeur Yazio pour un nutriment, "
    "n'estime PAS ce nutriment (Yazio prime, retourne null pour lui).\n\n"
    "Si AUCUNE boisson alcoolisee n'est nommee dans les repas, alcohol_g=0 "
    "(avec confidence=0.9) -- ce n'est pas une invention, c'est l'absence "
    "d'evidence.\n\n"
    "Reponds via l'outil JSON 'estimate_micros'."
)


TOOL_SCHEMA = {
    "name": "estimate_micros",
    "description": "Render the day-level micronutrient estimate.",
    "input_schema": {
        "type": "object",
        "properties": {
            "fat_sat_g": {
                "type": "object",
                "properties": {
                    "value": {"type": ["number", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string", "maxLength": 120},
                },
                "required": ["value", "confidence", "reason"],
            },
            "sodium_mg": {
                "type": "object",
                "properties": {
                    "value": {"type": ["number", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string", "maxLength": 120},
                },
                "required": ["value", "confidence", "reason"],
            },
            "sugar_g": {
                "type": "object",
                "properties": {
                    "value": {"type": ["number", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string", "maxLength": 120},
                },
                "required": ["value", "confidence", "reason"],
            },
            "fiber_g": {
                "type": "object",
                "properties": {
                    "value": {"type": ["number", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string", "maxLength": 120},
                },
                "required": ["value", "confidence", "reason"],
            },
            "alcohol_g": {
                "type": "object",
                "properties": {
                    "value": {"type": ["number", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string", "maxLength": 120},
                },
                "required": ["value", "confidence", "reason"],
            },
        },
        "required": list(NUTRIENT_KEYS),
    },
}


def _clamp(
    key: str,
    raw: Any,
    fat_g_total: float | None,
    carb_g_total: float | None,
) -> float | None:
    """Coerce raw to float and clamp into the physiological / parent-macro range.

    Returns None if raw is None, non-numeric, or lands outside the static range.
    """
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    lo, hi = STATIC_RANGES[key]
    # Dynamic parent-macro narrowing.
    if key == "fat_sat_g" and fat_g_total is not None and fat_g_total > 0:
        hi = min(hi, fat_g_total * 0.95)
    elif key == "sugar_g" and carb_g_total is not None and carb_g_total > 0:
        hi = min(hi, carb_g_total)
    elif key == "fiber_g" and carb_g_total is not None and carb_g_total > 0:
        hi = min(hi, carb_g_total * 0.4)
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _build_payload(
    day_iso: str,
    meals: list[dict],
    existing_micros: dict[str, float | None],
    daily_macros: dict[str, float | None],
) -> dict[str, Any]:
    """Slim payload for the LLM. Caps meal list at 40 entries."""
    slim_meals: list[dict] = []
    for m in (meals or [])[:40]:
        slim_meals.append(
            {
                "name": m.get("name") or m.get("meal"),
                "kcal": m.get("kcal"),
                "protein_g": m.get("protein_g"),
                "carb_g": m.get("carb_g"),
                "fat_g": m.get("fat_g"),
            }
        )
    return {
        "date": day_iso,
        "daily_totals": {
            "kcal": daily_macros.get("kcal"),
            "protein_g": daily_macros.get("protein_g"),
            "carb_g": daily_macros.get("carb_g"),
            "fat_g": daily_macros.get("fat_g"),
        },
        "existing_micros_from_yazio": {
            k: existing_micros.get(k) for k in NUTRIENT_KEYS
        },
        "meals": slim_meals,
        "instructions": (
            "Estime UNIQUEMENT les nutriments ou existing_micros_from_yazio "
            "vaut null. Pour les autres, retourne value=null reason='already from yazio'."
        ),
    }


def _call_llm(payload: dict[str, Any]) -> dict[str, Any] | None:
    """One-shot call to Haiku 4.5. Returns parsed tool input or None on any
    failure (missing key, SDK absent, API error, malformed response)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        print("  (enrich_estimation: anthropic SDK not installed)", file=sys.stderr)
        return None
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=SYSTEM_PROMPT,
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "estimate_micros"},
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                }
            ],
        )
        for block in resp.content or []:
            if (
                getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == "estimate_micros"
            ):
                raw = block.input
                if isinstance(raw, dict):
                    return raw
                if isinstance(raw, str):
                    return json.loads(raw)
        return None
    except Exception as e:
        print(f"  (enrich_estimation: LLM call failed: {e})", file=sys.stderr)
        return None


def estimate_day_micros(
    day_iso: str,
    meals: list[dict],
    existing_micros: dict[str, float | None],
    daily_macros: dict[str, float | None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Estimate missing micronutrients for one day.

    Args:
      day_iso: ISO date string (YYYY-MM-DD).
      meals: list of meal dicts, each with at least kcal + macros, optionally
        a `name`. Empty list disables the LLM call.
      existing_micros: keys = NUTRIENT_KEYS; value None means "missing, please
        estimate". Non-None values short-circuit (we never overwrite Yazio).
      daily_macros: totals for the day {kcal, protein_g, carb_g, fat_g}, used
        for parent-macro clamping. Optional; falls back to summing `meals`.

    Returns:
      {nutrient_key: {"value": float|None, "source": "llm_estimate",
                      "confidence": float, "reason": str}}
      Only contains entries for nutrients that were actually estimated
      (i.e. existing_micros[k] is None AND the LLM produced a non-null value).
    """
    if not meals:
        return {}
    # Which nutrients do we actually need?
    needed = [k for k in NUTRIENT_KEYS if existing_micros.get(k) is None]
    if not needed:
        return {}

    if daily_macros is None:
        daily_macros = {}
        for field in ("kcal", "protein_g", "carb_g", "fat_g"):
            total = 0.0
            seen = False
            for m in meals:
                v = m.get(field)
                if v is None:
                    continue
                try:
                    total += float(v)
                    seen = True
                except (TypeError, ValueError):
                    continue
            daily_macros[field] = total if seen else None

    payload = _build_payload(day_iso, meals, existing_micros, daily_macros)
    verdict = _call_llm(payload)
    if not verdict:
        return {}

    fat_g_total = daily_macros.get("fat_g")
    carb_g_total = daily_macros.get("carb_g")

    out: dict[str, dict[str, Any]] = {}
    for k in needed:
        entry = verdict.get(k) or {}
        raw_value = entry.get("value")
        confidence = entry.get("confidence", 0.0)
        reason = str(entry.get("reason") or "")[:120]
        clamped = _clamp(k, raw_value, fat_g_total, carb_g_total)
        if clamped is None:
            # LLM declined to estimate -- skip (column stays NULL).
            continue
        try:
            conf = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            conf = 0.0
        out[k] = {
            "value": clamped,
            "source": "llm_estimate",
            "confidence": conf,
            "reason": reason,
        }
    return out
