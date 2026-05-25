import type { Signal, SignalSpark } from "@/lib/types";

interface Props { signals: Signal[]; action?: string | null }

function Spark({ spark }: { spark: SignalSpark }) {
  const colorVar = spark.color === "ambre" ? "var(--ambre-brule)" : "var(--deep-sage)";
  if (spark.kind === "line") {
    const d = spark.points.map((p, i) => `${i === 0 ? "M" : "L"} ${p[0]} ${p[1]}`).join(" ");
    const last = spark.points[spark.points.length - 1];
    return (
      <svg className="signal-spark" viewBox="0 0 80 24" preserveAspectRatio="none" aria-hidden="true">
        <path d={d} fill="none" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
              vectorEffect="non-scaling-stroke" style={{ stroke: colorVar }}/>
        {spark.end_dot && <circle cx={last[0]} cy={last[1]} r="2" style={{ fill: colorVar }}/>}
      </svg>
    );
  }
  // bars
  const slot = 80 / spark.values.length;
  const barW = Math.min(3, slot - 2);
  return (
    <svg className="signal-spark" viewBox="0 0 80 24" preserveAspectRatio="none" aria-hidden="true">
      <g style={{ fill: colorVar, opacity: 0.85 }}>
        {spark.values.map((v, i) => (
          <rect
            key={i}
            x={2 + i * (slot)}
            y={24 - v}
            width={barW}
            height={v}
            opacity={spark.dim_indices?.includes(i) ? 0.4 : 1}
          />
        ))}
      </g>
    </svg>
  );
}

export function SignalsSection({ signals, action }: Props) {
  if (!signals.length) return null;
  return (
    <section className="signals" aria-labelledby="signals-heading">
      <header className="signals-head">
        <h3 id="signals-heading">Signaux</h3>
      </header>
      <ol className="signals-list">
        {signals.map(s => (
          <li key={s.id} className={"signal" + (s.status === "watch" ? " signal--watch" : "")}>
            <span className="signal-dot" aria-label={s.status === "watch" ? "à surveiller" : "ok"}></span>
            <div className="signal-body">
              <span className="signal-title">{s.title}</span>
              <span className="signal-sub">{s.sub}</span>
            </div>
            <span className="signal-value">
              {s.value}<span className="signal-unit">{s.unit}</span>
            </span>
            <Spark spark={s.spark} />
            <span className="signal-status">{s.status_label}</span>
          </li>
        ))}
      </ol>
      {action && (
        <div className="action-today" role="note">
          <span className="action-today-label">Action du jour</span>
          <span className="action-today-text">{action}</span>
        </div>
      )}
    </section>
  );
}
