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


def _summarize_food_items(food_items: list[dict] | None) -> list[dict] | None:
    """Group raw food items by meal slot for the LLM payload.

    Each entry: {slot, items: [{name, amount_g}]}. Capped at 60 named items
    total to keep the prompt cheap. The estimator uses these names verbatim
    to anchor its category-based heuristics (e.g. "sushi" -> sodium 1500+ mg).
    """
    if not food_items:
        return None
    buckets: dict[str, list[dict]] = {}
    total = 0
    for it in food_items:
        if total >= 60:
            break
        slot = it.get("meal") or it.get("meal_slot") or "?"
        name = it.get("name") or it.get("item_name")
        if not name:
            continue
        amount = it.get("amount_g")
        buckets.setdefault(slot, []).append(
            {"name": name, "amount_g": amount}
        )
        total += 1
    if not buckets:
        return None
    return [
        {"slot": slot, "items": items}
        for slot, items in buckets.items()
    ]


def _build_payload(
    day_iso: str,
    meals: list[dict],
    existing_micros: dict[str, float | None],
    daily_macros: dict[str, float | None],
    food_items: list[dict] | None = None,
) -> dict[str, Any]:
    """Slim payload for the LLM. Caps meal list at 40 entries.

    When `food_items` is provided, the named-ingredient summary is included
    alongside the meal totals so the model can ground sodium / SFA on
    actual products (e.g. "Sushi mix 250g" rather than "lunch 720 kcal").
    """
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
    payload: dict[str, Any] = {
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
            "vaut null. Pour les autres, retourne value=null reason='already from yazio'. "
            "Si food_items est fourni, base PRIORITAIREMENT tes estimations sur "
            "les noms d'ingredients (et leur quantite en grammes) plutot que sur "
            "les totaux meal-level -- ce sont les vraies entrees du journal."
        ),
    }
    fi_summary = _summarize_food_items(food_items)
    if fi_summary:
        payload["food_items"] = fi_summary
    return payload


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


# ---------- per-slot estimator (used when food_items are missing on a slot) ----

SLOT_SYSTEM_PROMPT = (
    "Tu es un nutritionniste qui ESTIME les micronutriments d'UN SEUL repas "
    "(slot Yazio: breakfast / lunch / dinner / snack) a partir de ses macros "
    "(kcal + proteines + glucides + lipides). Tu n'as PAS le nom des plats "
    "donc tu dois categoriser par PROFIL MACRO et appliquer des heuristiques "
    "physiologiques typiques. Reponds via l'outil JSON 'estimate_slot_micros'.\n\n"
    "PROFILS MACRO -> ESTIMATIONS TYPIQUES (par repas, pas par jour):\n"
    "- pasta-like (carb 50-80, fat 15-30, prot 30-50, ~600 kcal): "
    "sat 5-8 g, sodium 600-1200 mg, sugar 4-8 g, fiber 4-7 g\n"
    "- burger fast-food (fat 25-50, prot 25-40, carb 30-50, ~700-1000 kcal): "
    "sat 10-18 g, sodium 1000-2000 mg, sugar 5-10 g, fiber 2-4 g\n"
    "- salade composee (carb 10-30, fat 10-25, prot 20-40, ~400-600 kcal): "
    "sat 2-5 g, sodium 300-800 mg, sugar 5-15 g, fiber 5-10 g\n"
    "- sushi / asiatique (carb 50-80, fat 5-15, prot 20-40, ~500-700 kcal): "
    "sat 1-3 g, sodium 1500-3000 mg, sugar 10-20 g, fiber 2-5 g\n"
    "- viande grillee + accompagnement (prot 40-60, fat 15-30, carb 20-50, "
    "~600-800 kcal): sat 5-10 g, sodium 500-1000 mg, sugar 3-8 g, fiber 3-6 g\n"
    "- breakfast typique (prot 20-50, sucre 10-30): sat 3-8 g, sodium 200-600 mg, "
    "sugar 10-25 g, fiber 3-8 g\n"
    "- snack / dessert (sucre fort, sat variable): sat 3-10 g, sodium 100-400 mg, "
    "sugar 15-40 g, fiber 1-4 g\n\n"
    "BORNES PHYSIOLOGIQUES STRICTES (par repas):\n"
    "- fat_sat_g <= 0.95 * fat_g du repas\n"
    "- sodium_mg dans [0, 4000]\n"
    "- sugar_g <= 0.95 * carb_g du repas\n"
    "- fiber_g <= 0.4 * carb_g du repas\n"
    "- alcohol_g dans [0, 50] (rare en lunch/breakfast, plus probable en dinner)\n\n"
    "Si pas confiant sur la categorie (ex: macros tres atypiques), retourne "
    "confidence faible (<= 0.4). Le code python decidera s'il garde la valeur."
)


SLOT_TOOL_SCHEMA = {
    "name": "estimate_slot_micros",
    "description": "Estime les micros d'un seul repas a partir de son profil macro.",
    "input_schema": {
        "type": "object",
        "properties": {
            "fat_sat_g": {"type": "number", "minimum": 0},
            "sodium_mg": {"type": "number", "minimum": 0},
            "sugar_g": {"type": "number", "minimum": 0},
            "fiber_g": {"type": "number", "minimum": 0},
            "alcohol_g": {"type": "number", "minimum": 0},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason_fr": {"type": "string", "maxLength": 80},
        },
        "required": [
            "fat_sat_g",
            "sodium_mg",
            "sugar_g",
            "fiber_g",
            "alcohol_g",
            "confidence",
            "reason_fr",
        ],
    },
}


# Per-slot clamps. sodium upper bound is per-slot, not per-day.
SLOT_RANGES: dict[str, tuple[float, float]] = {
    "fat_sat_g": (0.0, 300.0),  # narrowed by 0.95 * fat below
    "sodium_mg": (0.0, 4000.0),
    "sugar_g": (0.0, 800.0),    # narrowed by 0.95 * carb below
    "fiber_g": (0.0, 200.0),    # narrowed by 0.4 * carb below
    "alcohol_g": (0.0, 50.0),
}

SLOT_NUTRIENT_KEYS = ("fat_sat_g", "sodium_mg", "sugar_g", "fiber_g", "alcohol_g")


def _clamp_slot(
    key: str, raw: Any, slot_fat: float | None, slot_carb: float | None
) -> float:
    """Per-slot clamp. Always returns a float (defaults to 0 if raw is None)."""
    if raw is None:
        return 0.0
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    lo, hi = SLOT_RANGES[key]
    if key == "fat_sat_g" and slot_fat is not None and slot_fat > 0:
        hi = min(hi, slot_fat * 0.95)
    elif key == "sugar_g" and slot_carb is not None and slot_carb > 0:
        hi = min(hi, slot_carb * 0.95)
    elif key == "fiber_g" and slot_carb is not None and slot_carb > 0:
        hi = min(hi, slot_carb * 0.4)
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


# In-memory cache per run: (date_iso, meal_slot) -> estimate dict
_SLOT_CACHE: dict[tuple[str, str], dict[str, float]] = {}


def estimate_slot_micros(
    date_iso: str,
    meal_slot: str,
    slot_kcal: float | None,
    slot_protein: float | None,
    slot_carb: float | None,
    slot_fat: float | None,
) -> dict[str, float]:
    """Estimate micros for ONE unresolved meal slot from its macros only.

    Returns {fat_sat_g, sodium_mg, sugar_g, fiber_g, alcohol_g, confidence,
    reason_fr}. Values are clamped to per-slot physiological bounds.

    Returns {} on any failure (no API key, SDK missing, API error). Caller
    must treat an empty dict as "no estimate available -- skip this slot".

    Cached per (date_iso, meal_slot) in process memory so multiple pipeline
    stages within a single run don't pay the LLM cost twice.
    """
    cache_key = (date_iso, meal_slot)
    if cache_key in _SLOT_CACHE:
        return _SLOT_CACHE[cache_key]

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        _SLOT_CACHE[cache_key] = {}
        return {}
    try:
        import anthropic
    except ImportError:
        print(
            "  (enrich_estimation: anthropic SDK not installed -- slot estimator skipped)",
            file=sys.stderr,
        )
        _SLOT_CACHE[cache_key] = {}
        return {}

    payload = {
        "meal_slot": meal_slot,
        "kcal": slot_kcal,
        "protein_g": slot_protein,
        "carb_g": slot_carb,
        "fat_g": slot_fat,
    }
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=400,
            system=SLOT_SYSTEM_PROMPT,
            tools=[SLOT_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "estimate_slot_micros"},
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                }
            ],
        )
        raw: dict[str, Any] | None = None
        for block in resp.content or []:
            if (
                getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == "estimate_slot_micros"
            ):
                inp = block.input
                if isinstance(inp, dict):
                    raw = inp
                elif isinstance(inp, str):
                    raw = json.loads(inp)
                break
        if raw is None:
            _SLOT_CACHE[cache_key] = {}
            return {}
    except Exception as e:
        print(
            f"  (enrich_estimation: slot LLM call failed [{date_iso}/{meal_slot}]: {e})",
            file=sys.stderr,
        )
        _SLOT_CACHE[cache_key] = {}
        return {}

    out: dict[str, float] = {}
    for k in SLOT_NUTRIENT_KEYS:
        out[k] = _clamp_slot(k, raw.get(k), slot_fat, slot_carb)
    try:
        out["confidence"] = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    out["reason_fr"] = str(raw.get("reason_fr") or "")[:80]
    _SLOT_CACHE[cache_key] = out
    return out


# ---------- per-item estimator (used for is_ai_estimate=true items) ----------

ITEM_SYSTEM_PROMPT = (
    "Tu es un nutritionniste qui ESTIME les micronutriments d'UN SEUL aliment "
    "log via Yazio AI photo / saisie libre. Tu connais le NOM de l'aliment, "
    "sa QUANTITE en grammes et ses macros (kcal/prot/carb/fat). Tu dois "
    "deduire fat_sat / sodium / sugar / fiber / alcohol pour cette portion "
    "specifique a partir de ta connaissance nutritionnelle.\n\n"
    "REGLES DE CATEGORISATION (toujours par la portion fournie):\n"
    "- Charcuterie / fromage affine / beurre / creme : sat 60-70% du fat, sodium 400-1200 mg/100g pour charcuterie\n"
    "- Plat asiatique / sushi / ramen / wok : sodium 600-1500 mg pour la portion\n"
    "- Burger / fast-food : sat ~25-35% du fat, sodium 400-900 mg\n"
    "- Pizza : sodium 600-1200 mg/part, sat 25-30% du fat\n"
    "- Salade composee maison / legumes : sodium 100-400 mg, fiber 3-8 g/100g de legumes\n"
    "- Plat prepare industriel / surgele / sauce : sodium >= 500 mg/portion typique\n"
    "- Pain / pates / riz : sodium 300-600 mg/100g pour pain, fiber 2-7 g/100g\n"
    "- Boisson alcoolisee (biere, vin, spiritueux): alcohol_g calcule a partir du volume * degre\n"
    "- Fruits / legumes frais : sugar 5-15 g/100g pour fruits, fiber 1-4 g/100g\n"
    "- Yaourt / lait / fromage blanc : sat selon teneur en fat, sugar 4-12 g/100g si sucre\n\n"
    "BORNES PHYSIOLOGIQUES STRICTES (par item):\n"
    "- fat_sat_g <= 0.95 * fat_g de l'item\n"
    "- sugar_g <= 0.95 * carb_g de l'item\n"
    "- fiber_g <= 0.4 * carb_g de l'item\n"
    "- alcohol_g = 0 sauf si le nom indique une boisson alcoolisee\n"
    "- sodium_mg dans [0, 4000] pour une portion unique\n\n"
    "Si le nom est trop vague (ex: 'plat', 'snack') -> confidence faible, "
    "valeurs prudentes (moyennes basses). Repond via l'outil 'estimate_item_micros'."
)


ITEM_TOOL_SCHEMA = {
    "name": "estimate_item_micros",
    "description": "Estime les micros pour UN aliment donne (nom + portion + macros).",
    "input_schema": {
        "type": "object",
        "properties": {
            "fat_sat_g": {"type": "number", "minimum": 0},
            "sodium_mg": {"type": "number", "minimum": 0},
            "sugar_g": {"type": "number", "minimum": 0},
            "fiber_g": {"type": "number", "minimum": 0},
            "alcohol_g": {"type": "number", "minimum": 0},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason_fr": {"type": "string", "maxLength": 80},
        },
        "required": [
            "fat_sat_g",
            "sodium_mg",
            "sugar_g",
            "fiber_g",
            "alcohol_g",
            "confidence",
            "reason_fr",
        ],
    },
}


ITEM_RANGES: dict[str, tuple[float, float]] = {
    "fat_sat_g": (0.0, 300.0),
    "sodium_mg": (0.0, 4000.0),
    "sugar_g": (0.0, 800.0),
    "fiber_g": (0.0, 200.0),
    "alcohol_g": (0.0, 100.0),
}

ITEM_NUTRIENT_KEYS = ("fat_sat_g", "sodium_mg", "sugar_g", "fiber_g", "alcohol_g")

# Process-level cache keyed by (name_lower, rounded_amount_g) so we don't pay
# the LLM cost twice for the same item logged across multiple days (e.g.
# "Spaghetti carbonara 350 g" repeated).
_ITEM_CACHE: dict[tuple[str, int], dict[str, float]] = {}

# In-process counter so the build script can report total LLM call volume.
_ITEM_CALL_COUNT = 0


def _clamp_item(
    key: str, raw: Any, fat_g: float | None, carb_g: float | None
) -> float:
    """Per-item physiological clamp. Defaults to 0 when raw is None."""
    if raw is None:
        return 0.0
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    lo, hi = ITEM_RANGES[key]
    if key == "fat_sat_g" and fat_g is not None and fat_g > 0:
        hi = min(hi, fat_g * 0.95)
    elif key == "sugar_g" and carb_g is not None and carb_g > 0:
        hi = min(hi, carb_g * 0.95)
    elif key == "fiber_g" and carb_g is not None and carb_g > 0:
        hi = min(hi, carb_g * 0.4)
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def get_item_call_count() -> int:
    """Number of LLM calls placed since process start (cache misses)."""
    return _ITEM_CALL_COUNT


def estimate_item_micros(
    name: str,
    amount_g: float,
    kcal: float | None,
    protein_g: float | None,
    carb_g: float | None,
    fat_g: float | None,
) -> dict[str, float]:
    """Estimate {fat_sat_g, sodium_mg, sugar_g, fiber_g, alcohol_g} for one item.

    Caches results per (name_lower, rounded_amount_g) so repeated items across
    days share one LLM call. Returns {} when the SDK / API key is missing or
    the call fails -- caller treats that as "no estimate, contributes 0".
    """
    global _ITEM_CALL_COUNT
    safe_name = (name or "").strip()
    if not safe_name:
        return {}
    try:
        amount_round = int(round(float(amount_g or 0)))
    except (TypeError, ValueError):
        amount_round = 0
    cache_key = (safe_name.lower(), amount_round)
    if cache_key in _ITEM_CACHE:
        return _ITEM_CACHE[cache_key]

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        _ITEM_CACHE[cache_key] = {}
        return {}
    try:
        import anthropic
    except ImportError:
        print(
            "  (enrich_estimation: anthropic SDK not installed -- item estimator skipped)",
            file=sys.stderr,
        )
        _ITEM_CACHE[cache_key] = {}
        return {}

    payload = {
        "name": safe_name,
        "amount_g": amount_g,
        "kcal": kcal,
        "protein_g": protein_g,
        "carb_g": carb_g,
        "fat_g": fat_g,
    }
    try:
        client = anthropic.Anthropic(api_key=key)
        _ITEM_CALL_COUNT += 1
        resp = client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=ITEM_SYSTEM_PROMPT,
            tools=[ITEM_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "estimate_item_micros"},
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                }
            ],
        )
        raw: dict[str, Any] | None = None
        for block in resp.content or []:
            if (
                getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == "estimate_item_micros"
            ):
                inp = block.input
                if isinstance(inp, dict):
                    raw = inp
                elif isinstance(inp, str):
                    raw = json.loads(inp)
                break
        if raw is None:
            _ITEM_CACHE[cache_key] = {}
            return {}
    except Exception as e:
        print(
            f"  (enrich_estimation: item LLM call failed [{safe_name[:40]}]: {e})",
            file=sys.stderr,
        )
        _ITEM_CACHE[cache_key] = {}
        return {}

    out: dict[str, float] = {}
    for k in ITEM_NUTRIENT_KEYS:
        out[k] = _clamp_item(k, raw.get(k), fat_g, carb_g)
    try:
        out["confidence"] = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    _ITEM_CACHE[cache_key] = out
    return out


def estimate_day_micros(
    day_iso: str,
    meals: list[dict],
    existing_micros: dict[str, float | None],
    daily_macros: dict[str, float | None] | None = None,
    food_items: list[dict] | None = None,
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
    if not meals and not food_items:
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

    payload = _build_payload(
        day_iso, meals, existing_micros, daily_macros, food_items=food_items
    )
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
