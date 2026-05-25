"""Famille 4 — projection LDL prochain bilan (Mensink-Katan).

Lit le dernier LDL connu via supabase (lab_result/lab_panel), regarde la
fenêtre depuis ce bilan, calcule la dérive prédite de LDL en fonction du
changement de %E SFA et PUFA.

Formule Mensink-Katan 2003 (méta de 60 essais contrôlés):
  ΔLDL (mg/dL) ≈ 1.28 × Δ%E_SFA − 0.24 × Δ%E_PUFA − 1.46 × Δ(chol_mg/1000kcal)
On ignore le 3e terme tant qu'on n'a pas le cholestérol alimentaire.

Référence pré-bilan: on prend 10%E_SFA (la cible AHA est <=6 mais Yannis
historiquement tourne autour de 10) comme baseline si on n'a pas de
mesure alimentaire pré-prise de sang.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from ..scoring import (
    SEVERITY_DOWNSCOPE,
    InsightCandidate,
    adjusted_measured_ratio,
    recency_from_last_date,
)

REFERENCE_PCT_E_SAT = 10.0
REFERENCE_PCT_E_PUFA = 6.0
LDL_TARGET = 100.0  # mg/dL — cible "optimal" non-haut-risque
MIN_DELTA_LDL = 5.0  # mg/dL — seuil de déclenchement


def _fetch_last_ldl(sb_client: Any) -> dict[str, Any] | None:
    """Returns {value: mg/dL, collected_at: date} or None.

    sb_client exposes .request(method, table, params=...) → list[dict]
    matching the thin wrapper in detect.py.
    """
    try:
        rows = sb_client.request(
            "GET",
            "lab_result",
            params={
                "select": "value_num,unit,marker_code,marker_label,panel_id",
                "marker_code": "ilike.*LDL*",
                "order": "id.desc",
                "limit": "50",
            },
        )
    except Exception:
        return None
    if not rows:
        # fallback: try by label
        try:
            rows = sb_client.request(
                "GET",
                "lab_result",
                params={
                    "select": "value_num,unit,marker_code,marker_label,panel_id",
                    "marker_label": "ilike.*LDL*",
                    "order": "id.desc",
                    "limit": "50",
                },
            )
        except Exception:
            return None
    if not rows:
        return None

    # Fetch the most recent panel date for each panel_id we have.
    panel_ids = list({r["panel_id"] for r in rows if r.get("panel_id")})
    panels: dict[str, str] = {}
    for pid in panel_ids:
        try:
            p = sb_client.request(
                "GET",
                "lab_panel",
                params={"select": "id,collected_at", "id": f"eq.{pid}"},
            )
            if p:
                panels[pid] = p[0]["collected_at"]
        except Exception:
            continue

    best: tuple[datetime, dict[str, Any]] | None = None
    for r in rows:
        pid = r.get("panel_id")
        if not pid or pid not in panels:
            continue
        if r.get("value_num") is None:
            continue
        ts = panels[pid]
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue
        value = float(r["value_num"])
        unit = (r.get("unit") or "").lower()
        if "mmol" in unit:
            value = value * 38.67
        if best is None or dt > best[0]:
            best = (dt, {"value": value, "collected_at": dt.date()})
    if best is None:
        return None
    return best[1]


def detect(df: pd.DataFrame, today: date, sb_client: Any) -> list[InsightCandidate]:
    if df.empty or "pct_e_sat" not in df.columns:
        return []
    last_ldl = _fetch_last_ldl(sb_client)
    if last_ldl is None:
        return []

    since = last_ldl["collected_at"]
    win = df[pd.to_datetime(df["date"]).dt.date > since].copy()
    # Only consider logged days for the SFA mean (else zeros from non-loggés
    # bias the projection toward "everything is fine, LDL stable").
    if "is_logged" in win.columns:
        win = win[win["is_logged"] == True]  # noqa: E712
    win = win.dropna(subset=["pct_e_sat"])
    if len(win) < 14:
        return []

    mean_sat = float(win["pct_e_sat"].mean())
    mean_pufa = float(win.get("pct_e_pufa", pd.Series(dtype=float)).mean() or REFERENCE_PCT_E_PUFA)

    d_sat = mean_sat - REFERENCE_PCT_E_SAT
    d_pufa = mean_pufa - REFERENCE_PCT_E_PUFA
    delta_ldl = 1.28 * d_sat - 0.24 * d_pufa

    projected = last_ldl["value"] + delta_ldl
    crosses_target = (last_ldl["value"] <= LDL_TARGET) and (projected > LDL_TARGET)

    if abs(delta_ldl) < MIN_DELTA_LDL and not crosses_target:
        return []

    coverage = adjusted_measured_ratio(win, "fat_sat_g_source")
    # Projection requires at least some grounded SFA signal.
    if coverage < 0.3:
        return []

    if projected > 130:
        severity = "alert"
    elif projected > LDL_TARGET or abs(delta_ldl) >= 10:
        severity = "watch"
    else:
        severity = "info"
    if coverage < 0.5:
        severity = SEVERITY_DOWNSCOPE.get(severity, severity)

    magnitude = min(15.0, abs(delta_ldl))
    last_d = pd.to_datetime(win["date"].max()).date()
    n_days = len(win)

    if coverage < 0.5:
        title = f"LDL projeté (estimé) : ~{projected:.0f} mg/dL"
    else:
        title = f"LDL projeté : ~{projected:.0f} mg/dL"
    body = f"Dernier bilan {last_ldl['value']:.0f}, dérive {delta_ldl:+.0f} sur {n_days} j de suivi."
    if coverage < 0.7:
        body += " · estimation basée sur SFA partiellement estimés"

    return [
        InsightCandidate(
            detector_key="mensink_ldl_projection",
            family=4,
            severity=severity,
            title=title,
            body=body,
            bucket=f"since-{since.isoformat()}",
            data={
                "last_ldl_mg_dl": round(last_ldl["value"], 1),
                "last_ldl_date": since.isoformat(),
                "n_days_window": n_days,
                "mean_pct_e_sat": round(mean_sat, 2),
                "mean_pct_e_pufa": round(mean_pufa, 2),
                "delta_pct_e_sat_vs_ref": round(d_sat, 2),
                "delta_pct_e_pufa_vs_ref": round(d_pufa, 2),
                "projected_ldl_mg_dl": round(projected, 1),
                "delta_ldl_mg_dl": round(delta_ldl, 1),
                "ref_pct_e_sat": REFERENCE_PCT_E_SAT,
                "ref_pct_e_pufa": REFERENCE_PCT_E_PUFA,
                "formula": "ΔLDL = 1.28·ΔSFA%E − 0.24·ΔPUFA%E (Mensink-Katan 2003)",
                "coverage": round(coverage, 3),
            },
            metric_keys=["pct_e_sat", "pct_e_pufa"],
            link_href="/detail/biology",
            magnitude_bonus=magnitude,
            recency_bonus=recency_from_last_date(last_d, today),
        )
    ]
