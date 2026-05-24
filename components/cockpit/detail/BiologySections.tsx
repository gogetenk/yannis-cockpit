"use client";
import { useMemo, useState } from "react";
import type { BiologySection } from "@/lib/types";
import { BiologyGauge } from "./BiologyGauge";

interface Props { sections: BiologySection[] }

export function BiologySections({ sections }: Props) {
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();

  const filtered = useMemo(() => {
    if (!needle) return sections;
    return sections
      .map(sec => ({
        ...sec,
        markers: sec.markers.filter(m =>
          m.label.toLowerCase().includes(needle) ||
          m.code.toLowerCase().includes(needle)
        ),
      }))
      .filter(sec => sec.markers.length > 0);
  }, [sections, needle]);

  if (!sections?.length) return null;
  const totalShown = filtered.reduce((n, s) => n + s.markers.length, 0);

  return (
    <section className="bio-sections" aria-labelledby="bio-sections-heading">
      <div className="bio-search">
        <input
          type="search"
          placeholder="Rechercher un marqueur (ex: LDL, ferritine, ALT)"
          value={q}
          onChange={e => setQ(e.target.value)}
          aria-label="Filtrer les marqueurs"
        />
        {needle && <span className="bio-search-count">{totalShown} résultat{totalShown > 1 ? "s" : ""}</span>}
      </div>
      {filtered.map(sec => {
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
                    {(m.ref_low !== null || m.ref_high !== null) && (
                      <span className="bio-marker-ref">
                        {m.ref_low !== null && m.ref_high !== null
                          ? `${m.ref_low}–${m.ref_high}`
                          : m.ref_high !== null ? `< ${m.ref_high}` : `> ${m.ref_low}`} {m.unit}
                      </span>
                    )}
                  </div>
                  <div className="bio-marker-value">
                    <span className="bio-marker-num">{m.value}</span>
                    <span className="bio-marker-unit">{m.unit}</span>
                  </div>
                  <BiologyGauge m={m} />
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
      {filtered.length === 0 && (
        <p className="bio-search-empty">Aucun marqueur ne contient « {q} ».</p>
      )}
    </section>
  );
}
