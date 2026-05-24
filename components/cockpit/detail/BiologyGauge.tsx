import type { BiologyMarker } from "@/lib/types";

interface Props { m: BiologyMarker }

/** Mini-jauge horizontale : track gris, bande verte = range normal, dot pleine
 *  = valeur actuelle, dot creuse = baseline. Rendue en HTML/CSS pour garder
 *  les dots parfaitement ronds (SVG preserveAspectRatio=none les écrase). */
export function BiologyGauge({ m }: Props) {
  if (m.value_num === null) return null;
  const hasLow = m.ref_low !== null;
  const hasHigh = m.ref_high !== null;
  if (!hasLow && !hasHigh) return null;

  const refLow = hasLow ? (m.ref_low as number) : 0;
  const refHigh = hasHigh ? (m.ref_high as number) : refLow * 2;
  const v = m.value_num;
  const b = m.baseline_num;
  const candidates = [v, refHigh, ...(b !== null ? [b] : [])];
  const visMax = Math.max(...candidates) * 1.1;
  const visMin = hasLow ? Math.min(refLow * 0.7, v * 0.9) : 0;
  const span = visMax - visMin || 1;
  const xPct = (x: number) => Math.max(0, Math.min(100, ((x - visMin) / span) * 100));

  const lowPct = hasLow ? xPct(refLow) : 0;
  const highPct = xPct(refHigh);
  const valPct = xPct(v);
  const basePct = b !== null ? xPct(b) : null;

  return (
    <div className="bio-gauge" aria-hidden>
      <div className="bio-gauge-track" />
      <div
        className="bio-gauge-normal"
        style={{ left: `${lowPct}%`, width: `${Math.max(0, highPct - lowPct)}%` }}
      />
      {basePct !== null && (
        <div className="bio-gauge-baseline" style={{ left: `${basePct}%` }} />
      )}
      <div
        className={"bio-gauge-value" + (m.flag ? " off" : "")}
        style={{ left: `${valPct}%` }}
      />
    </div>
  );
}
