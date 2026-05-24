import type { BiologySection } from "@/lib/types";

interface Props { sections: BiologySection[] }

export function BiologySections({ sections }: Props) {
  if (!sections?.length) return null;
  return (
    <section className="bio-sections" aria-labelledby="bio-sections-heading">
      <h2 id="bio-sections-heading" className="section-label">Tous les marqueurs</h2>
      {sections.map(sec => {
        const abnormal = sec.markers.filter(m => m.flag).length;
        return (
          <div key={sec.key} className="bio-section">
            <header className="bio-section-head">
              <h3>{sec.label}</h3>
              <span className="bio-section-count">
                {sec.markers.length} marqueur{sec.markers.length > 1 ? "s" : ""}
                {abnormal > 0 && <span className="bio-section-flag"> · {abnormal} hors plage</span>}
              </span>
            </header>
            <ul className="bio-marker-list">
              {sec.markers.map(m => (
                <li key={m.code} className={"bio-marker" + (m.flag ? " bio-marker--off bio-marker--" + m.flag.toLowerCase() : "")}>
                  <div className="bio-marker-name">
                    <span className="bio-marker-label">{m.label}</span>
                    {(m.ref_low || m.ref_high) && (
                      <span className="bio-marker-ref">
                        {m.ref_low && m.ref_high ? `${m.ref_low}–${m.ref_high}` : m.ref_high ? `< ${m.ref_high}` : `> ${m.ref_low}`} {m.unit}
                      </span>
                    )}
                  </div>
                  <div className="bio-marker-value">
                    <span className="bio-marker-num">{m.value}</span>
                    <span className="bio-marker-unit">{m.unit}</span>
                  </div>
                  {m.delta_pct !== null && (
                    <div className={"bio-marker-delta " + (Math.abs(m.delta_pct) < 5 ? "neutral" : m.delta_pct < 0 ? "down" : "up")}>
                      {m.delta_pct >= 0 ? "+" : "−"}{Math.abs(m.delta_pct).toFixed(1).replace(".", ",")} %
                      <span className="bio-marker-baseline"> vs baseline</span>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </section>
  );
}
