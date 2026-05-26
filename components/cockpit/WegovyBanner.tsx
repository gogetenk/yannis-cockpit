"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { WegovyTitration } from "@/lib/types";

interface Props { wegovy: WegovyTitration }

export function WegovyBanner({ wegovy }: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const n = wegovy.ladder.length;
  const xPos = (i: number) => 20 + (i / (n - 1)) * 260;
  const completedEndX = xPos(wegovy.step_index - 1);
  const fmtDose = (mg: number) => mg.toString().replace(".", ",");

  const daysSince = wegovy.days_since_last_injection ?? 0;
  const daysToNext = wegovy.days_to_next_injection ?? 0;
  const overdue = !!wegovy.is_overdue;
  const unknown = !!wegovy.last_injection_unknown;
  const showLogBtn = unknown || daysSince >= 6 || overdue;
  const topRight = unknown
    ? "dernière injection inconnue"
    : `dernière ${wegovy.last_injection_label ?? "dose"} ${daysSince === 0 ? "(aujourd'hui)" : daysSince === 1 ? "(hier)" : `(il y a ${daysSince} j)`}`;

  async function logInjection(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const today = new Date();
      const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
      const r = await fetch("/api/log-injection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date: iso, dose_mg: wegovy.current_dose_mg }),
      });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(t.slice(0, 120) || `HTTP ${r.status}`);
      }
      router.refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "erreur");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Link href="/detail/wegovy" className="wegovy wegovy--link" aria-labelledby="wegovy-label">
      <div className="wegovy-top">
        <span className="label label--ink" id="wegovy-label">Wegovy · J + {wegovy.day_since_start} <span className="card-chevron" aria-hidden>›</span></span>
        <span className="now"><strong>{fmtDose(wegovy.current_dose_mg)} mg</strong> · {topRight}</span>
      </div>

      <svg className="wegovy-ladder" viewBox="0 0 300 44" role="img"
           aria-label={`Échelle de titration: étape ${wegovy.step_index} sur ${n}, dose actuelle ${fmtDose(wegovy.current_dose_mg)} mg`}>
        <line x1="20" y1="14" x2="280" y2="14" style={{ stroke: "var(--sage-mist)", strokeWidth: 1.5 }} strokeLinecap="round"/>
        <line x1="20" y1="14" x2={completedEndX} y2="14" style={{ stroke: "var(--deep-sage)", strokeWidth: 1.5 }} strokeLinecap="round"/>

        {wegovy.ladder.map((step, i) => {
          const cx = xPos(i);
          if (step.status === "current") {
            return (
              <g key={i}>
                <circle cx={cx} cy="14" r="6" style={{ fill: "var(--deep-sage)" }}/>
                <circle cx={cx} cy="14" r="9" fill="none" style={{ stroke: "var(--deep-sage)", strokeOpacity: 0.25, strokeWidth: 1.5 }}/>
              </g>
            );
          }
          if (step.status === "done") {
            return <circle key={i} cx={cx} cy="14" r="4" style={{ fill: "var(--deep-sage)" }}/>;
          }
          return <circle key={i} cx={cx} cy="14" r="3.5" style={{ fill: "var(--warm-cream)", stroke: "var(--sage-mist)", strokeWidth: 1.5 }}/>;
        })}

        <g fontSize="9.5" fontWeight="500" style={{ fill: "var(--sage-ash)", fontVariantNumeric: "tabular-nums" }}>
          {wegovy.ladder.map((step, i) => (
            <text
              key={i}
              x={xPos(i)}
              y="36"
              textAnchor="middle"
              style={step.status === "current" ? { fill: "var(--sage-ink)", fontWeight: 500 } : undefined}
            >{fmtDose(step.dose_mg)}</text>
          ))}
        </g>
      </svg>

      <div className="wegovy-foot">
        <span>
          {unknown ? (
            <>cadence inconnue · log ton injection · </>
          ) : overdue ? (
            <>injection en retard ({daysSince} j) · </>
          ) : wegovy.days_to_next_injection !== undefined && (
            <>prochaine injection {daysToNext === 0 ? "aujourd'hui" : daysToNext === 1 ? "demain" : daysToNext < 0 ? `il y a ${-daysToNext} j` : `dans ${daysToNext} j`} · </>
          )}
          prochaine titration · <strong>{fmtDose(wegovy.next_dose_mg)} mg</strong> dans {wegovy.next_in_weeks} sem.
        </span>
        {showLogBtn && (
          <button
            type="button"
            onClick={logInjection}
            disabled={busy}
            className={`wegovy-log-btn ${overdue ? "wegovy-log-btn--overdue" : ""}`}
            aria-label="Confirmer l'injection d'aujourd'hui"
          >
            {busy ? "…" : err ? "réessayer" : "✓ pris aujourd'hui"}
          </button>
        )}
      </div>
    </Link>
  );
}
