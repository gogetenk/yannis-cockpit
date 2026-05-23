import type { SubTrajectory } from "@/lib/types";

interface Props { subs: SubTrajectory[] }

const VBX = 280;
const VBY = 100;

function pathFor(points: { value: number }[], yMin: number, yMax: number) {
  const xPad = 8;
  const n = points.length;
  const yTop = 16, yBot = VBY - 24;
  const xAt = (i: number) => xPad + (i / Math.max(1, n - 1)) * (VBX - 2 * xPad);
  const yAt = (v: number) => yTop + ((yMax - v) / (yMax - yMin)) * (yBot - yTop);
  const d = points.map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(p.value).toFixed(1)}`).join(" ");
  const last = { x: xAt(n - 1), y: yAt(points[n - 1].value) };
  return { d, last, xAt, yAt };
}

export function SubTrajectories({ subs }: Props) {
  return (
    <section className="subs" aria-label="Sous-métriques">
      {subs.map(sub => {
        const ys = sub.points.map(p => p.value);
        const pad = (Math.max(...ys) - Math.min(...ys)) * 0.15 || 1;
        const yMin = Math.min(...ys) - pad;
        const yMax = Math.max(...ys) + pad;
        const { d, last } = pathFor(sub.points, yMin, yMax);
        const strokeColor = sub.ambre ? "var(--ambre-brule)" : "var(--deep-sage)";
        return (
          <article className="sub" key={sub.key}>
            <header className="sub-head">
              <span className="label label--ink">{sub.label}</span>
              <span className="sub-trend">{sub.trend_label}</span>
            </header>
            <p className="sub-figure">
              {sub.current}<span className="unit"> {sub.unit}</span>
            </p>
            <svg viewBox={`0 0 ${VBX} ${VBY}`} preserveAspectRatio="none" role="img" aria-label={`${sub.label} trend`}>
              <path d={d} fill="none" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"
                    style={{ stroke: strokeColor }} vectorEffect="non-scaling-stroke" />
              <circle cx={last.x} cy={last.y} r="2.6" style={{ fill: strokeColor }} />
            </svg>
          </article>
        );
      })}
    </section>
  );
}
