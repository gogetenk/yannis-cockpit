"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { WeightHero } from "@/lib/types";
import { fmtDateLong, fmtDelta, fmtKg } from "@/lib/utils";

interface Props { hero: WeightHero }

const VIEW = { xMin: 30, xMax: 330, yMin: 10, yMax: 170 };

// Gompertz refit on Wilding 2021 STEP-1. Must stay in lockstep with
// ingest/snapshot/build_snapshot.py (ASYMPTOTE_KG, GOMP_TAU_WEEKS, GOMP_SHAPE).
// Asymptote 74 (not 75) is the STEP-1 plateau projected onto Yannis' 86.6 kg
// baseline; 75 kg is the user goal, finite-time reachable.
const ASYMP = 74;
const START = 86.6;
const SHAPE = 1.4;
function weightIdeal(t: number) { return ASYMP + (START - ASYMP) * Math.exp(-Math.pow(t / 20, SHAPE)); }
function weightProjected(t: number) { return ASYMP + (START - ASYMP) * Math.exp(-Math.pow(t / 18, SHAPE)); }

function deltaPhrase(delta: number, tolerance: number): string {
  // Negative delta = lighter than ideal = ahead of plan.
  const a = Math.abs(delta).toFixed(1).replace(".", ",");
  if (Math.abs(delta) <= tolerance / 2) return "sur la trajectoire idéale";
  if (delta < 0) return `${a} kg en avance sur la cible`;
  return `${a} kg vs cible idéale`;
}

export function Hero({ hero }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const svgRef  = useRef<SVGSVGElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<null | {
    xView: number; yIdeal: number; date: Date;
    ideal: number; actual: number; delta: number; isFuture: boolean;
    left: number;
  }>(null);

  const startDate = useMemo(() => new Date(hero.start_date), [hero.start_date]);
  const { kg_min, kg_max, week_min, week_max } = hero.range;

  const xToWeek = useCallback((xView: number) => {
    const f = (xView - VIEW.xMin) / (VIEW.xMax - VIEW.xMin);
    return Math.max(week_min, Math.min(week_max, f * (week_max - week_min)));
  }, [week_min, week_max]);
  const weekToX = useCallback((t: number) => VIEW.xMin + (t / week_max) * (VIEW.xMax - VIEW.xMin), [week_max]);
  const wToY = useCallback((w: number) => VIEW.yMin + ((kg_max - w) / (kg_max - kg_min)) * (VIEW.yMax - VIEW.yMin), [kg_min, kg_max]);

  const weightActual = useCallback((t: number) => {
    const pts = hero.real_points;
    if (t <= pts[0].week) return pts[0].kg;
    if (t >= pts[pts.length-1].week) return pts[pts.length-1].kg;
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i-1], b = pts[i];
      if (t <= b.week) {
        const f = (t - a.week) / (b.week - a.week);
        return a.kg + f * (b.kg - a.kg);
      }
    }
    return pts[pts.length-1].kg;
  }, [hero.real_points]);

  const weekToDate = useCallback((t: number) => {
    const d = new Date(startDate);
    d.setDate(d.getDate() + Math.round(t * 7));
    return d;
  }, [startDate]);

  const idealPath = useMemo(() => {
    const samples: string[] = [];
    for (let w = 0; w <= week_max; w += 2) {
      samples.push(`${samples.length === 0 ? "M" : "L"} ${weekToX(w).toFixed(1)} ${wToY(weightIdeal(w)).toFixed(1)}`);
    }
    return samples.join(" ");
  }, [weekToX, wToY, week_max]);

  const projectionPath = useMemo(() => {
    const samples: string[] = [];
    const start = hero.today_week;
    for (let w = start; w <= week_max; w += 2) {
      samples.push(`${samples.length === 0 ? "M" : "L"} ${weekToX(w).toFixed(1)} ${wToY(weightProjected(w)).toFixed(1)}`);
    }
    return samples.join(" ");
  }, [weekToX, wToY, week_max, hero.today_week]);

  const tolerancePath = useMemo(() => {
    const upper: string[] = [], lower: string[] = [];
    for (let w = 0; w <= week_max; w += 2) {
      upper.push(`${upper.length === 0 ? "M" : "L"} ${weekToX(w).toFixed(1)} ${wToY(weightIdeal(w) + hero.tolerance_kg).toFixed(1)}`);
    }
    for (let w = week_max; w >= 0; w -= 2) {
      lower.push(`L ${weekToX(w).toFixed(1)} ${wToY(weightIdeal(w) - hero.tolerance_kg).toFixed(1)}`);
    }
    return upper.join(" ") + " " + lower.join(" ") + " Z";
  }, [weekToX, wToY, week_max, hero.tolerance_kg]);

  const realPath = useMemo(() => {
    return hero.real_points.map((p, i) => `${i === 0 ? "M" : "L"} ${weekToX(p.week).toFixed(1)} ${wToY(p.kg).toFixed(1)}`).join(" ");
  }, [hero.real_points, weekToX, wToY]);

  const todayX = weekToX(hero.today_week);
  const todayY = wToY(hero.current_kg);
  const etaX = weekToX(hero.today_week + (week_max - hero.today_week)); // not strictly correct; use real eta
  // Compute eta X from ideal: find when ideal crosses 75 within range.
  const etaWeeks = useMemo(() => {
    for (let w = 0; w <= week_max + 4; w += 0.1) {
      if (weightProjected(w) <= 75) return w;
    }
    return week_max;
  }, [week_max]);
  const etaXcoord = weekToX(Math.min(etaWeeks, week_max));
  const etaYcoord = wToY(75);

  const pointerToViewX = useCallback((clientX: number) => {
    if (!svgRef.current) return VIEW.xMin;
    const rect = svgRef.current.getBoundingClientRect();
    const xPx = clientX - rect.left;
    return (xPx / rect.width) * 350;
  }, []);

  const update = useCallback((clientX: number) => {
    if (!wrapRef.current) return;
    const xView = pointerToViewX(clientX);
    const t = xToWeek(xView);
    const isFuture = t > hero.today_week;
    const ideal = weightIdeal(t);
    const actual = isFuture ? weightProjected(t) : weightActual(t);
    const delta = actual - ideal;
    const wrapRect = wrapRef.current.getBoundingClientRect();
    const ttWidth = tooltipRef.current?.getBoundingClientRect().width || 180;
    const xPx = clientX - wrapRect.left;
    let left = xPx - ttWidth / 2;
    const pad = 8;
    if (left < pad) left = pad;
    if (left + ttWidth > wrapRect.width - pad) left = wrapRect.width - ttWidth - pad;
    setTip({ xView, yIdeal: wToY(ideal), date: weekToDate(t), ideal, actual, delta, isFuture, left });
  }, [pointerToViewX, xToWeek, hero.today_week, weightActual, wToY, weekToDate]);

  const hide = useCallback(() => setTip(null), []);

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const onTouchMove = (e: TouchEvent) => {
      if (e.touches[0]) { update(e.touches[0].clientX); e.preventDefault(); }
    };
    wrap.addEventListener("touchmove", onTouchMove, { passive: false });
    return () => wrap.removeEventListener("touchmove", onTouchMove);
  }, [update]);

  return (
    <section className="hero" aria-labelledby="hero-title">
      <div className="hero-head">
        <span className="label" id="hero-title">Poids</span>
        <p className={"status" + (hero.status !== "conforme" ? " deviation" : "")}>{hero.statusLabel}</p>
        <p className="delta">{deltaPhrase(hero.delta_kg, hero.tolerance_kg)}</p>
        <p className="figure">{fmtKg(hero.current_kg)}</p>
      </div>

      <div
        className="chart-wrap"
        ref={wrapRef}
        onMouseLeave={hide}
        onTouchStart={(e) => { if (e.touches[0]) update(e.touches[0].clientX); }}
        onTouchEnd={() => setTimeout(hide, 1500)}
      >
        <svg
          ref={svgRef}
          className="trajectory"
          viewBox="0 0 350 200"
          role="img"
          aria-label={`Trajectoire poids: ${fmtKg(hero.current_kg)} actuel`}
          onMouseMove={(e) => update(e.clientX)}
        >
          <defs>
            <linearGradient id="proj-fade" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%"   stopColor="var(--cream-on-sage)" stopOpacity="0.6"/>
              <stop offset="100%" stopColor="var(--cream-on-sage)" stopOpacity="0.18"/>
            </linearGradient>
          </defs>

          {/* Y axis: kg_min=73, kg_max=87 → 14 kg / 160 px = 11.43 px per kg */}
          <g fontSize="9" fontWeight="500" style={{ fill: "var(--cream-on-sage-soft)" }}>
            <text x="330" y="14" textAnchor="end">87</text>
            <text x="330" y="80.7" textAnchor="end">81</text>
            <text x="330" y={wToY(75).toFixed(1)} textAnchor="end">75</text>
          </g>

          {/* Goal line at 75 kg */}
          <line x1="30" y1={wToY(75).toFixed(1)} x2="330" y2={wToY(75).toFixed(1)} strokeWidth="0.5" strokeDasharray="2 3" opacity="0.45" style={{ stroke: "var(--cream-on-sage-soft)" }}/>

          {/* X axis labels */}
          <g fontSize="9" fontWeight="500" letterSpacing="0.06em" style={{ fill: "var(--cream-on-sage-soft)" }}>
            <text x="30"   y="194" textAnchor="start">AVR</text>
            <text x="98.5" y="194" textAnchor="middle">JUIN</text>
            <text x="180"  y="194" textAnchor="middle">SEPT</text>
            <text x="260"  y="194" textAnchor="middle">DÉC</text>
            <text x="330"  y="194" textAnchor="end">AVR'27</text>
          </g>

          <path d={tolerancePath} opacity="0.07" style={{ fill: "var(--cream-on-sage)" }}/>
          <path d={idealPath} fill="none" strokeOpacity="0.35" strokeWidth="1.25" strokeDasharray="3 4" strokeLinecap="round" strokeLinejoin="round" style={{ stroke: "var(--cream-on-sage)" }}/>
          <path d={projectionPath} fill="none" stroke="url(#proj-fade)" strokeWidth="1.5" strokeDasharray="4 5" strokeLinecap="round" strokeLinejoin="round"/>
          <path d={realPath} fill="none" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" style={{ stroke: "var(--cream-on-sage)" }}/>

          <circle cx={todayX} cy={todayY} r="3.5" style={{ fill: "var(--cream-on-sage)" }}/>
          <circle cx={todayX} cy={todayY} r="6" fill="none" strokeOpacity="0.35" strokeWidth="1" style={{ stroke: "var(--cream-on-sage)" }}/>

          <line x1={etaXcoord} y1={etaYcoord} x2={etaXcoord} y2="183" strokeOpacity="0.45" strokeWidth="1" style={{ stroke: "var(--cream-on-sage)" }}/>
          <circle cx={etaXcoord} cy={etaYcoord} r="2.5" fillOpacity="0.65" style={{ fill: "var(--cream-on-sage)" }}/>

          {tip && (
            <>
              <line x1={tip.xView} y1="10" x2={tip.xView} y2="170" strokeWidth="0.8" strokeDasharray="2 3" style={{ stroke: "var(--cream-on-sage)", strokeOpacity: 0.7 }}/>
              <circle cx={tip.xView} cy={tip.yIdeal} r="4" style={{ fill: "var(--cream-on-sage)", stroke: "var(--deep-sage)", strokeWidth: 1.5 }}/>
            </>
          )}
        </svg>

        <div
          ref={tooltipRef}
          className="chart-tooltip"
          hidden={!tip}
          role="status"
          aria-live="polite"
          style={tip ? { left: `${tip.left}px`, top: "8px" } : undefined}
        >
          <div className="tt-date">{tip ? fmtDateLong(tip.date) : ""}</div>
          <div className="tt-row">
            <span className="tt-label">idéale</span>
            <span className="tt-value tt-ideal">{tip ? fmtKg(tip.ideal) : ""}</span>
          </div>
          <div className="tt-row">
            <span className="tt-label tt-label-actual">{tip?.isFuture ? "projetée" : "mesurée"}</span>
            <span className="tt-value tt-actual">{tip ? fmtKg(tip.actual) : ""}</span>
          </div>
          <div className="tt-row">
            <span className="tt-label tt-label-delta">{tip?.isFuture ? "écart proj." : "écart"}</span>
            <span className={"tt-value tt-delta " + (tip && Math.abs(tip.delta) > hero.tolerance_kg ? "off" : "on")}>{tip ? fmtDelta(tip.delta) : ""}</span>
          </div>
        </div>
      </div>

      <div className="hero-foot">
        <div className="eta-left">
          <span className="eta-label">ETA 75 kg</span>
          <span className="eta-date">≈ {fmtDateLong(new Date(hero.eta_75kg))}</span>
        </div>
      </div>
    </section>
  );
}
