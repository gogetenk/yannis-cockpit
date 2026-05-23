import type { DetailTrajectory as Tr } from "@/lib/types";

interface Props { trajectory: Tr; height?: number }

const VBX = 700;
const VBY = 280;

function mkScales(t: Tr) {
  const xPad = 36;
  const xMin = xPad;
  const xMax = VBX - xPad;
  const yMin = 24;
  const yMax = VBY - 36;
  const n = t.points.length;
  const xAt = (i: number) => xMin + (i / Math.max(1, n - 1)) * (xMax - xMin);
  const yAt = (v: number) => yMin + ((t.y_max - v) / (t.y_max - t.y_min)) * (yMax - yMin);
  return { xAt, yAt, xMin, xMax, yMin, yMax };
}

export function DetailTrajectory({ trajectory: t }: Props) {
  const { xAt, yAt, xMin, xMax, yMin, yMax } = mkScales(t);

  const realPath = t.points.map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(p.value).toFixed(1)}`).join(" ");
  const idealPath = t.ideal?.map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(p.value).toFixed(1)}`).join(" ");
  const tolPath = t.ideal && t.tolerance
    ? (() => {
        const up = t.ideal!.map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(p.value + t.tolerance!).toFixed(1)}`).join(" ");
        const down = [...t.ideal!].reverse().map((p, i) => `L ${xAt(t.ideal!.length - 1 - i).toFixed(1)} ${yAt(p.value - t.tolerance!).toFixed(1)}`).join(" ");
        return `${up} ${down} Z`;
      })()
    : null;

  const last = t.points[t.points.length - 1];
  const lastX = xAt(t.points.length - 1);
  const lastY = yAt(last.value);

  // Y-axis ticks: 3 evenly spaced.
  const yTicks = [t.y_max, (t.y_max + t.y_min) / 2, t.y_min];
  // X-axis ticks: 4 from points (first, ~1/3, ~2/3, last) — use date label.
  const xIdx = [0, Math.floor((t.points.length - 1) / 3), Math.floor(2 * (t.points.length - 1) / 3), t.points.length - 1];
  const xTicks = xIdx.map(i => ({ x: xAt(i), label: t.points[i]?.date ?? "" }));

  const targetY = t.target ? yAt(t.target.value) : null;

  return (
    <figure className="detail-trajectory" aria-label={`Trajectoire ${t.y_unit}`}>
      <svg viewBox={`0 0 ${VBX} ${VBY}`} role="img" preserveAspectRatio="none">
        {/* gridlines y */}
        <g style={{ stroke: "var(--sage-mist)" }} strokeWidth="0.5">
          {yTicks.map((v, i) => <line key={i} x1={xMin} y1={yAt(v)} x2={xMax} y2={yAt(v)} />)}
        </g>

        {/* tolerance band */}
        {tolPath && (
          <path d={tolPath} opacity="0.08" style={{ fill: "var(--deep-sage)" }} />
        )}

        {/* ideal */}
        {idealPath && (
          <path d={idealPath} fill="none" strokeWidth="1.2" strokeDasharray="4 5"
                strokeLinecap="round" strokeLinejoin="round"
                style={{ stroke: "var(--sage-ash)" }} vectorEffect="non-scaling-stroke" />
        )}

        {/* target line */}
        {targetY !== null && t.target && (
          <>
            <line x1={xMin} y1={targetY} x2={xMax} y2={targetY}
                  strokeWidth="0.8" strokeDasharray="3 4" opacity="0.7"
                  style={{ stroke: "var(--deep-sage)" }} vectorEffect="non-scaling-stroke" />
            <text x={xMax - 4} y={targetY - 6} textAnchor="end" fontSize="11"
                  style={{ fill: "var(--sage-ash)", fontVariantNumeric: "tabular-nums" }}>
              {t.target.label}
            </text>
          </>
        )}

        {/* real */}
        <path d={realPath} fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              style={{ stroke: "var(--deep-sage)" }} vectorEffect="non-scaling-stroke" />

        {/* end dot */}
        <circle cx={lastX} cy={lastY} r="4" style={{ fill: "var(--deep-sage)" }} />
        <circle cx={lastX} cy={lastY} r="7" fill="none" strokeOpacity="0.3" strokeWidth="1"
                style={{ stroke: "var(--deep-sage)" }} />

        {/* y labels */}
        <g fontSize="11" style={{ fill: "var(--sage-ash)", fontVariantNumeric: "tabular-nums" }}>
          {yTicks.map((v, i) => (
            <text key={i} x={xMin - 6} y={yAt(v) + 4} textAnchor="end">
              {Number.isInteger(v) ? v : v.toFixed(1).replace(".", ",")}
            </text>
          ))}
        </g>

        {/* x labels */}
        <g fontSize="11" letterSpacing="0.04em"
           style={{ fill: "var(--sage-ash)", fontVariantNumeric: "tabular-nums" }}>
          {xTicks.map((t, i) => (
            <text key={i} x={t.x} y={VBY - 8} textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"}>
              {t.label}
            </text>
          ))}
        </g>
      </svg>
    </figure>
  );
}
