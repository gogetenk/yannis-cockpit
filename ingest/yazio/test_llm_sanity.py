"""Smoke test for llm_sanity: verify the physiological clamp via a stub."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from unittest.mock import patch

# Make `ingest.yazio.sanitize` importable as a stub if the real module
# doesn't exist yet (the parallel agent owns it).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from ingest.yazio.sanitize import Correction  # type: ignore
except Exception:  # pragma: no cover - stub for standalone test
    import types

    @dataclass
    class Correction:  # type: ignore[no-redef]
        date: date
        nutrient_id: str
        raw_value: float | None
        sanitized_value: float | None
        source: str
        rule_key: str
        reason: str
        llm_model: str | None = None
        llm_confidence: float | None = None

    stub = types.ModuleType("ingest.yazio.sanitize")
    stub.Correction = Correction  # type: ignore[attr-defined]
    sys.modules.setdefault("ingest.yazio.sanitize", stub)

from ingest.yazio import llm_sanity  # noqa: E402


def _make_correction() -> Correction:
    return Correction(
        date=date(2026, 5, 1),
        nutrient_id="alcohol",
        raw_value=330.0,
        sanitized_value=None,
        source="rule",
        rule_key="alcohol_kcal_coherence",
        reason="raw 330g/day exceeds 150g cap",
    )


def test_clamp_rejects_out_of_range_refined_value():
    """If LLM proposes 500g alcohol, it must be rejected and rule kept."""
    fake_verdict = {
        "plausible": False,
        "refined_value": 500.0,  # well over 150g cap
        "confidence": 0.9,
        "reason_fr": "test",
    }
    with patch.object(llm_sanity, "_call_llm", return_value=fake_verdict):
        result = llm_sanity.review_correction(_make_correction(), [], 1846.0)
    assert result.sanitized_value is None  # original deterministic drop kept
    assert result.source == "rule"


def test_clamp_accepts_in_range_refined_value():
    fake_verdict = {
        "plausible": False,
        "refined_value": 40.0,
        "confidence": 0.8,
        "reason_fr": "4 verres de vin",
    }
    with patch.object(llm_sanity, "_call_llm", return_value=fake_verdict):
        result = llm_sanity.review_correction(_make_correction(), [], 1846.0)
    assert result.sanitized_value == 40.0
    assert result.source == "llm"
    assert result.llm_model == llm_sanity.MODEL
    assert result.llm_confidence == 0.8


def test_llm_veto_keeps_raw_value():
    fake_verdict = {
        "plausible": True,
        "refined_value": None,
        "confidence": 0.7,
        "reason_fr": "330g cohérent avec 12 bières fortes",
    }
    with patch.object(llm_sanity, "_call_llm", return_value=fake_verdict):
        result = llm_sanity.review_correction(_make_correction(), [], 1846.0)
    assert result.sanitized_value == 330.0
    assert result.source == "llm"


def test_api_failure_falls_back_to_rule():
    with patch.object(llm_sanity, "_call_llm", return_value=None):
        corr = _make_correction()
        result = llm_sanity.review_correction(corr, [], 1846.0)
    assert result is corr  # unchanged
    assert result.source == "rule"


if __name__ == "__main__":
    test_clamp_rejects_out_of_range_refined_value()
    test_clamp_accepts_in_range_refined_value()
    test_llm_veto_keeps_raw_value()
    test_api_failure_falls_back_to_rule()
    print("all tests passed")
