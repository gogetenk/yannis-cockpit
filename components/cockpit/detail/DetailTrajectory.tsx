import type { DetailTrajectory as Tr } from "@/lib/types";

interface Props { trajectory: Tr; height?: number }

const VBX = 700;
const VBY = 280;

interface Pt { date: string; value: number }
function isFinitePoint(p: { date: string; value: number | null | undefined }): p is Pt {
  return p.value !== null && p.value !== undefined && Number.isFinite(p.value);
}

function mkScales(yMinScale: number, yMaxScale: number, nPoints: number) {
  const xPad = 36;
  const xMin = xPad;
  const xMax = VBX - xPad;
  const yMin = 24;
  const yMax = VBY - 36;
  const xAt = (i: number) => xMin + (i / Math.max(1, nPoints - 1)) * (xMax - xMin);
  const yAt = (v: number) => yMin + ((yMaxScale - v) / (yMaxScale - yMinScale)) * (yMax - yMin);
  return { xAt, yAt, xMin, xMax, yMin, yMax };
}

export function DetailTrajectory({ trajectory: t }: Props) {
  // Filter null / non-finite points before drawing — bare API data may carry
  // gaps that would otherwise produce isolated spikes on an empty band.
  const points: Pt[] = (t.points as Array<{ date: string; value: number | null | undefined }>).filter(isFinitePoint);
  const ideal: Pt[] | undefined = t.ideal
    ? (t.ideal as Array<{ date: string; value: number | null | undefined }>).filter(isFinitePoint)
    : undefined;

  if (points.length === 0) {
    return (
      <figure className="detail-trajectory" aria-label="Trajectoire indisponible">
        <p className="detail-empty" style={{ padding: "24px 8px" }}>Pas de mesures sur la fenêtre.</p>
      </figure>
    );
  }

  // Auto-calibrate Y to actual data range when declared y_min/y_max are far
  // outside, otherwise the line collapses into a flat ribbon. We keep the
  // declared range only if it brackets the data tightly (≤ 25% padding above
  // data span on either side), else we recompute around data + ideal + target.
  const ys = points.map(p => p.value);
  if (ideal) ys.push(...ideal.map(p => p.value));
  if (t.target) ys.push(t.target.value);
  const dataMin = Math.min(...ys);
  const dataMax = Math.max(...ys);
  const dataSpan = Math.max(1e-6, dataMax - dataMin);
  const declaredSpan = Math.max(1e-6, t.y_max - t.y_min);
  const padBelow = dataMin - t.y_min;
  const padAbove = t.y_max - dataMax;
  const tooMuchPad = padBelow > dataSpan * 0.5 || padAbove > dataSpan * 0.5 || declaredSpan > dataSpan * 3;

  let yMinScale: number;
  let yMaxScale: number;
  if (tooMuchPad) {
    const margin = dataSpan * 0.18 || 1;
    yMinScale = dataMin - margin;
    yMaxScale = dataMax + margin;
  } else {
    yMinScale = t.y_min;
    yMaxScale = t.y_max;
  }

  const n = points.length;
  const { xAt, yAt, xMin, xMax, yMin, yMax } = mkScales(yMinScale, yMaxScale, n);

  const realPath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(p.value).toFixed(1)}`).join(" ");
  const idealPath = ideal?.map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(p.value).toFixed(1)}`).join(" ");
  const tolPath = ideal && t.tolerance
    ? (() => {
        const up = ideal.map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(p.value + t.tolerance!).toFixed(1)}`).join(" ");
        const down = [...ideal].reverse().map((p, i) => `L ${xAt(ideal.length - 1 - i).toFixed(1)} ${yAt(p.value - t.tolerance!).toFixed(1)}`).join(" ");
        return `${up} ${down} Z`;
      })()
    : null;

  const last = points[points.length - 1];
  const lastX = xAt(points.length - 1);
  const lastY = yAt(last.value);

  // Y-axis ticks: 3 evenly spaced on the visible range.
  const yTicks = [yMaxScale, (yMaxScale + yMinScale) / 2, yMinScale];
  // X-axis ticks: up to 4 unique indices (degenerate if <4 points).
  const xIdxSet = new Set(
    n <= 1 ? [0]
    : n === 2 ? [0, 1]
    : n === 3 ? [0, 1, 2]
    : [0, Math.floor((n - 1) / 3), Math.floor(2 * (n - 1) / 3), n - 1]
  );
  const xIdx = [...xIdxSet].sort((a, b) => a - b);
  const xTicks = xIdx.map(i => ({ x: xAt(i), label: points[i]?.date ?? "" }));

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
            <text x={xMin + 30} y={targetY - 6} textAnchor="start" fontSize="11"
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
