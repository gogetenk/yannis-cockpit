import Link from "next/link";
import type { WegovyTitration } from "@/lib/types";

interface Props { wegovy: WegovyTitration }

export function WegovyBanner({ wegovy }: Props) {
  const n = wegovy.ladder.length;
  const xPos = (i: number) => 20 + (i / (n - 1)) * 260;
  // Completed track goes from first dot to current step.
  const completedEndX = xPos(wegovy.step_index - 1);
  const fmtDose = (mg: number) => mg.toString().replace(".", ",");

  return (
    <Link href="/detail/wegovy" className="wegovy wegovy--link" aria-labelledby="wegovy-label">
      <div className="wegovy-top">
        <span className="label label--ink" id="wegovy-label">Wegovy · J + {wegovy.day_since_start} <span className="card-chevron" aria-hidden>›</span></span>
        <span className="now"><strong>{fmtDose(wegovy.current_dose_mg)} mg</strong> · aujourd&apos;hui</span>
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
        <span>prochaine titration · <strong>{fmtDose(wegovy.next_dose_mg)} mg</strong> dans {wegovy.next_in_weeks} sem.</span>
      </div>
    </Link>
  );
}
