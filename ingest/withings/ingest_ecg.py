"""
Withings ECG waveform → HRV metrics → Supabase `withings_ecg`.

The standard measure ingest only captures the afib *result code* (type 130).
This pulls the raw single-lead waveform via the Withings Heart endpoint and
derives the time-domain HRV metrics the result code can't give us:

  v2/heart action=list   → ECG recordings (signalid, afib, avg HR, timestamp)
  v2/heart action=get    → raw signal array (µV) + sampling_frequency

For each new signalid we detect R-peaks on the waveform (pure-Python
Pan-Tompkins-lite: detrend → derivative → square → moving-window integrate →
adaptive threshold with refractory), build the RR series, filter ectopics, and
compute RMSSD / SDNN / pNN50 / mean HR. Withings' own average HR for the strip
is stored alongside as a ground-truth check (our mean_hr should match it).

NOTE on interpretation: a Withings ECG is a ~30s *standing spot* recording, so
its RMSSD is posture- and moment-dependent — it is NOT overnight resting HRV.
Treat it as a spot autonomic snapshot, not a recovery baseline.

Idempotent: skips signalids already in `withings_ecg`.

Env vars: WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET, SUPABASE_URL,
          SUPABASE_SERVICE_ROLE_KEY
          ECG_SINCE_DAYS   Optional list window (default 400).

Run `python ingest/withings/ingest_ecg.py --selftest` to validate the HRV math
on a synthetic ECG (no network/credentials needed).
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

# Reuse the Withings OAuth + Supabase helpers from the sibling ingest module.
# When run as `python ingest/withings/ingest_ecg.py`, this script's directory is
# on sys.path[0], so the bare import resolves.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest_withings import (  # noqa: E402
    env,
    load_token,
    refresh_if_needed,
    sb_get,
    sb_headers,
)

HEART_URL = "https://wbsapi.withings.net/v2/heart"


# ───────────────────────── signal processing ──────────────────────────


def _moving_average(x: list[float], w: int) -> list[float]:
    """Centered moving average via prefix sums. O(n)."""
    n = len(x)
    if w <= 1 or n == 0:
        return list(x)
    pref = [0.0] * (n + 1)
    for i in range(n):
        pref[i + 1] = pref[i] + x[i]
    half = w // 2
    out = [0.0] * n
    for i in range(n):
        a = max(0, i - half)
        b = min(n, i + half + 1)
        out[i] = (pref[b] - pref[a]) / (b - a)
    return out


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(q * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def detect_rpeaks(signal: list[float], fs: float) -> list[int]:
    """Return R-peak sample indices. Pan-Tompkins-lite tuned for a clean,
    short (~30s) single-lead Withings strip."""
    n = len(signal)
    if n < int(fs * 3):  # need at least 3 seconds
        return []
    # 1. Baseline wander removal: subtract a ~0.6s moving average.
    base = _moving_average(signal, max(1, int(0.6 * fs)))
    detr = [signal[i] - base[i] for i in range(n)]
    # 2. 5-point derivative (emphasises the steep QRS slope).
    deriv = [0.0] * n
    for i in range(2, n - 2):
        deriv[i] = (2 * detr[i + 1] + detr[i + 2] - detr[i - 2] - 2 * detr[i - 1]) / 8.0
    # 3. Square.
    sq = [d * d for d in deriv]
    # 4. Moving-window integration (~0.10s).
    mwi = _moving_average(sq, max(1, int(0.10 * fs)))
    # 5. Adaptive threshold from the integrated signal distribution.
    nz = sorted(v for v in mwi if v > 0)
    if not nz:
        return []
    thr = 0.40 * _percentile(nz, 0.98)
    refractory = int(0.28 * fs)  # ≤ ~210 bpm
    search = max(1, int(0.05 * fs))  # refine window on the raw signal
    peaks: list[int] = []
    i = 1
    while i < n - 1:
        if mwi[i] > thr and mwi[i] >= mwi[i - 1] and mwi[i] >= mwi[i + 1]:
            # Refine to the local max of |detrended| signal nearby (sharp R).
            a = max(0, i - search)
            b = min(n, i + search + 1)
            r = max(range(a, b), key=lambda k: abs(detr[k]))
            if peaks and r - peaks[-1] < refractory:
                # Two candidates inside the refractory window: keep the taller.
                if abs(detr[r]) > abs(detr[peaks[-1]]):
                    peaks[-1] = r
            else:
                peaks.append(r)
            i += refractory
        else:
            i += 1
    return peaks


def hrv_metrics(peaks: list[int], fs: float) -> dict:
    """RR series → time-domain HRV. Filters non-physiological + ectopic beats."""
    if len(peaks) < 4:
        return {"n_beats": len(peaks), "quality": "insufficient"}
    rr = [(peaks[i + 1] - peaks[i]) / fs * 1000.0 for i in range(len(peaks) - 1)]
    # Physiological gate: 30–200 bpm.
    rr = [x for x in rr if 300.0 <= x <= 2000.0]
    if len(rr) < 3:
        return {"n_beats": len(peaks), "quality": "insufficient"}
    # Ectopic filter: drop beats deviating > 20% from the running median.
    med = sorted(rr)[len(rr) // 2]
    nn = [x for x in rr if abs(x - med) <= 0.20 * med]
    if len(nn) < 3:
        nn = rr  # fall back if the gate was too aggressive
    mean_rr = sum(nn) / len(nn)
    var = sum((x - mean_rr) ** 2 for x in nn) / (len(nn) - 1)
    sdnn = math.sqrt(var)
    succ = [nn[i + 1] - nn[i] for i in range(len(nn) - 1)]
    rmssd = math.sqrt(sum(d * d for d in succ) / len(succ)) if succ else 0.0
    pnn50 = 100.0 * sum(1 for d in succ if abs(d) > 50.0) / len(succ) if succ else 0.0
    rejected = len(rr) - len(nn)
    quality = "ok"
    if rejected > 0.25 * len(rr) or sdnn > 250:
        quality = "noisy"
    return {
        "n_beats": len(peaks),
        "mean_hr": round(60000.0 / mean_rr, 1),
        "mean_rr_ms": round(mean_rr, 1),
        "sdnn_ms": round(sdnn, 1),
        "rmssd_ms": round(rmssd, 1),
        "pnn50_pct": round(pnn50, 1),
        "quality": quality,
    }


# ─────────────────────────── Withings API ─────────────────────────────


def fetch_heart_list(access_token: str, startdate: int, enddate: int):
    offset = 0
    while True:
        q = {"action": "list", "startdate": startdate, "enddate": enddate}
        if offset:
            q["offset"] = offset
        r = requests.post(HEART_URL, data=q, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != 0:
            sys.exit(f"heart/list failed: {json.dumps(data)[:500]}")
        body = data["body"]
        for s in body.get("series", []):
            yield s
        if not body.get("more"):
            return
        offset = body.get("offset", 0)


def fetch_signal(access_token: str, signalid: int) -> dict:
    r = requests.post(
        HEART_URL,
        data={"action": "get", "signalid": signalid},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=45,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("status") != 0:
        sys.exit(f"heart/get {signalid} failed: {json.dumps(data)[:500]}")
    return data["body"]


def _existing_signal_ids() -> set[int]:
    rows = sb_get("withings_ecg", {"select": "signal_id"})
    return {int(r["signal_id"]) for r in rows}


def _extra_interval(extra: dict, *keys) -> float | None:
    for k in keys:
        v = extra.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def sb_upsert_ecg(row: dict) -> None:
    url = f"{env('SUPABASE_URL')}/rest/v1/withings_ecg?on_conflict=signal_id"
    headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"}
    resp = requests.post(url, headers=headers, data=json.dumps([row], default=str), timeout=60)
    if not resp.ok:
        sys.exit(f"upsert withings_ecg failed {resp.status_code}: {resp.text[:500]}")


def build_row(entry: dict, body: dict) -> dict:
    ecg = entry.get("ecg") or {}
    signal_id = int(ecg.get("signalid"))
    signal = [float(v) for v in body.get("signal", [])]
    fs = float(body.get("sampling_frequency") or 0) or 300.0
    extra = body.get("extra_data") or {}
    ts = datetime.fromtimestamp(int(entry["timestamp"]), tz=timezone.utc).isoformat()

    peaks = detect_rpeaks(signal, fs)
    hrv = hrv_metrics(peaks, fs)

    # QTc via Bazett if we have QT + a usable RR (Withings sometimes ships these
    # in extra_data; otherwise leave null rather than fabricate).
    qt = _extra_interval(extra, "qt_interval", "qt", "qt_duration")
    qtc = None
    if qt and hrv.get("mean_rr_ms"):
        qtc = round(qt / math.sqrt(hrv["mean_rr_ms"] / 1000.0), 1)

    return {
        "signal_id": signal_id,
        "ts": ts,
        "afib_result": ecg.get("afib"),
        "device_model": entry.get("model"),
        "withings_hr": entry.get("heart_rate"),
        "sampling_hz": int(fs),
        "duration_s": round(len(signal) / fs, 1) if signal else None,
        "n_beats": hrv.get("n_beats"),
        "mean_hr": hrv.get("mean_hr"),
        "mean_rr_ms": hrv.get("mean_rr_ms"),
        "sdnn_ms": hrv.get("sdnn_ms"),
        "rmssd_ms": hrv.get("rmssd_ms"),
        "pnn50_pct": hrv.get("pnn50_pct"),
        "qrs_ms": _extra_interval(extra, "qrs_interval", "qrs", "qrs_duration"),
        "pr_ms": _extra_interval(extra, "pr_interval", "pr"),
        "qt_ms": qt,
        "qtc_ms": qtc,
        "quality": hrv.get("quality"),
        "raw_signal": signal,
        "extra": {"list_entry": entry, "extra_data": extra},
    }


def main() -> None:
    for k in ("WITHINGS_CLIENT_ID", "WITHINGS_CLIENT_SECRET", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        env(k)
    tok = refresh_if_needed(load_token())
    access = tok["access_token"]

    since_days = int(os.environ.get("ECG_SINCE_DAYS") or 400)
    end = int(datetime.now(timezone.utc).timestamp())
    start = int((datetime.now(timezone.utc) - timedelta(days=since_days)).timestamp())
    print(f"→ heart/list window {since_days}d ({start} → {end})", file=sys.stderr)

    existing = _existing_signal_ids()
    print(f"  {len(existing)} ECG signal(s) already ingested", file=sys.stderr)

    seen, ingested = 0, 0
    for entry in fetch_heart_list(access, start, end):
        ecg = entry.get("ecg") or {}
        sid = ecg.get("signalid")
        if sid is None:
            continue  # BP-only Heart entry, no ECG waveform
        seen += 1
        if int(sid) in existing:
            continue
        body = fetch_signal(access, int(sid))
        row = build_row(entry, body)
        sb_upsert_ecg(row)
        ingested += 1
        print(
            f"  ✓ signal {sid} @ {row['ts'][:19]}  "
            f"HR {row['mean_hr']} (Withings {row['withings_hr']})  "
            f"RMSSD {row['rmssd_ms']}ms  SDNN {row['sdnn_ms']}ms  "
            f"afib={row['afib_result']}  [{row['quality']}]",
            file=sys.stderr,
        )
    print(f"  done. {seen} ECG entries, {ingested} newly ingested.", file=sys.stderr)


# ─────────────────────────────── selftest ─────────────────────────────


def _synthetic_ecg(rr_ms: list[float], fs: float) -> list[float]:
    """Build a clean synthetic ECG (QRS spikes + baseline wander) from an RR
    series, so detect_rpeaks/hrv_metrics can be validated end-to-end."""
    beat_times = [0.0]
    for rr in rr_ms:
        beat_times.append(beat_times[-1] + rr / 1000.0)
    total_s = beat_times[-1] + 1.0
    n = int(total_s * fs)
    sig = [0.0] * n
    for i in range(n):
        t = i / fs
        # slow baseline wander
        sig[i] += 40.0 * math.sin(2 * math.pi * 0.25 * t)
    for bt in beat_times:
        center = int(bt * fs)
        width = max(1, int(0.02 * fs))  # ~20ms sharp QRS
        for k in range(-3 * width, 3 * width + 1):
            idx = center + k
            if 0 <= idx < n:
                sig[idx] += 1000.0 * math.exp(-(k * k) / (2.0 * width * width))
    return sig


def _selftest() -> int:
    fs = 300.0
    # Known RR series: mean ~882ms (≈68 bpm) with deliberate beat-to-beat jitter.
    rr = [820, 900, 860, 940, 800, 910, 870, 930, 840, 900,
          880, 850, 920, 810, 905, 865, 935, 845, 915, 875,
          925, 835, 895, 855, 905, 885, 815, 945, 825, 935]
    succ = [rr[i + 1] - rr[i] for i in range(len(rr) - 1)]
    true_rmssd = math.sqrt(sum(d * d for d in succ) / len(succ))
    true_hr = 60000.0 / (sum(rr) / len(rr))

    sig = _synthetic_ecg([float(x) for x in rr], fs)
    peaks = detect_rpeaks(sig, fs)
    m = hrv_metrics(peaks, fs)

    print(f"selftest: injected {len(rr) + 1} beats, detected {m.get('n_beats')}")
    print(f"  HR     true {true_hr:.1f}  detected {m.get('mean_hr')}")
    print(f"  RMSSD  true {true_rmssd:.1f}  detected {m.get('rmssd_ms')}")
    print(f"  SDNN   {m.get('sdnn_ms')}  pNN50 {m.get('pnn50_pct')}  quality {m.get('quality')}")

    ok = (
        m.get("n_beats") == len(rr) + 1
        and abs(m["mean_hr"] - true_hr) < 1.0
        and abs(m["rmssd_ms"] - true_rmssd) < 5.0
    )
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    main()
