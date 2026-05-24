import type { BiologyMarker } from "@/lib/types";

interface Props { m: BiologyMarker }

/** Mini-jauge horizontale: bande verte = range normal, dot pleine = valeur
 *  actuelle, dot creuse = baseline si dispo. Échelle visuelle: 0 ou
 *  ref_low*0.7 → max(ref_high*1.3, value*1.1, baseline*1.1). */
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
    <svg className="bio-gauge" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden>
      <rect x="0" y="3" width="100" height="2" rx="1" className="bio-gauge-track" />
      <rect x={lowPct} y="2.5" width={highPct - lowPct} height="3" rx="1.5" className="bio-gauge-normal" />
      {basePct !== null && <circle cx={basePct} cy="4" r="1.4" className="bio-gauge-baseline" />}
      <circle cx={valPct} cy="4" r="2.2" className={"bio-gauge-value" + (m.flag ? " off" : "")} />
    </svg>
  );
}
