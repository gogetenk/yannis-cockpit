import type { BioAge } from "@/lib/types";

interface Props { bioAge: BioAge }

// Sub-age bar height encodes age on a zoomed scale around the actual sub-age
// range so 6–10 yr deltas read as full-height differences instead of vanishing
// into a 0–70 yr canvas. Chrono is included so its line stays anchored.
function makeSubageHeight(values: number[], chrono: number) {
  const all = [...values, chrono];
  const min = Math.min(...all) - 3;
  const max = Math.max(...all) + 3;
  const span = Math.max(1, max - min);
  return (age: number) => Math.max(8, Math.min(100, ((age - min) / span) * 100));
}

export function BioAgeTile({ bioAge }: Props) {
  const ages = bioAge.trajectory_12m;
  const subageHeightPct = makeSubageHeight(bioAge.subages.map(s => s.value), bioAge.chrono);
  // Map trajectory_12m values (years) to SVG viewBox 0–240 × 0–64.
  // Y-scale: 70 → 0 (top), 0 → 64 (bottom). Chrono baseline at y=24 (≈ 35 yrs).
  // Y-scale tuned to a tight window around the trajectory + chrono, so the
  // line uses the full 64px viewBox height instead of compressing into 2px.
  const values = [...ages.map(p => p.value), bioAge.chrono];
  const yMin = Math.min(...values) - 2;
  const yMax = Math.max(...values) + 2;
  const yScale = (v: number) => 4 + ((yMax - v) / (yMax - yMin)) * 56;
  const xs = ages.map((_, i) => (i / (ages.length - 1)) * 232 + 4);
  const ys = ages.map(p => yScale(p.value));
  const pathD = ages.map((_, i) => `${i === 0 ? "M" : "L"} ${xs[i].toFixed(1)} ${ys[i].toFixed(1)}`).join(" ");
  const last = { x: xs[xs.length - 1], y: ys[ys.length - 1] };
  const first = { x: xs[0], y: ys[0] };
  const chronoY = yScale(bioAge.chrono);

  // Axis labels: pick first, mid, last from trajectory months.
  const axisLabels = [
    ages[0]?.month ?? "",
    ages[Math.floor(ages.length / 2)]?.month ?? "",
    ages[ages.length - 1]?.month ?? "",
  ];

  return (
    <article className="tile tile--wide" aria-labelledby="t-age">
      <header className="tile-head">
        <span className="label" id="t-age">Âge biologique</span>
        <span className="meta">12 mois</span>
      </header>

      <div className="ages">
        <span className="real">{bioAge.composite}<span className="unit"> ans</span></span>
        <span className="vs-inline">{bioAge.delta_vs_chrono > 0 ? "+" : ""}{bioAge.delta_vs_chrono} vs chrono</span>
      </div>

      <div className="subages" aria-label="5 sous-âges biologiques">
        {bioAge.subages.map(sub => {
          const chronoTop = 100 - subageHeightPct(bioAge.chrono);
          return (
          <div className="subage" key={sub.key}>
            <div className="subage-bar">
              <span
                className={"subage-fill" + (sub.off ? " off" : "")}
                style={{ height: `${subageHeightPct(sub.value)}%` }}
              ></span>
              <span className="subage-chrono" style={{ top: `${chronoTop}%` }}></span>
            </div>
            <span className={"subage-val" + (sub.off ? " off" : "")}>{sub.value}</span>
            <span className="subage-label">{sub.label}</span>
          </div>
          );
        })}
      </div>

      <figure className="age-trajectory" aria-label={`Évolution âge biologique 12 mois: ${ages[0]?.value} → ${ages[ages.length-1]?.value}`}>
        <div className="age-trajectory-chart">
          <span className="age-trajectory-baseline-label">chrono {bioAge.chrono}</span>
          <svg viewBox="0 0 240 64" preserveAspectRatio="none" role="img">
            <line x1="0" y1={chronoY} x2="240" y2={chronoY} strokeWidth="0.6"
                  strokeDasharray="3 4" opacity="0.5" style={{ stroke: "var(--sage-ash)" }}/>
            <path d={pathD}
                  fill="none" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round"
                  vectorEffect="non-scaling-stroke"
                  style={{ stroke: "var(--deep-sage)" }}/>
            <circle cx={first.x} cy={first.y} r="2.5" opacity="0.45" style={{ fill: "var(--deep-sage)" }}/>
            <circle cx={last.x} cy={last.y} r="3.4" style={{ fill: "var(--deep-sage)" }}/>
          </svg>
        </div>
        <figcaption className="age-trajectory-axis">
          {axisLabels.map((l, i) => <span key={i}>{l}</span>)}
        </figcaption>
      </figure>
    </article>
  );
}
