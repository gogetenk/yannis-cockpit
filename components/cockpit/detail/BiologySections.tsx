"use client";
import { useMemo, useState } from "react";
import type { BiologySection, BiologyMarker } from "@/lib/types";
import { BiologyGauge } from "./BiologyGauge";

interface Props { sections: BiologySection[] }

function MarkerRow({ m }: { m: BiologyMarker }) {
  return (
    <li className={"bio-marker" + (m.flag ? " bio-marker--off bio-marker--" + m.flag.toLowerCase() : "")}>
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
  );
}

export function BiologySections({ sections }: Props) {
  const [q, setQ] = useState("");
  const [showNormal, setShowNormal] = useState(false);
  const needle = q.trim().toLowerCase();

  // Triage by status.
  const { abnormal, normal } = useMemo(() => {
    const all: { marker: BiologyMarker; category: string }[] = [];
    sections.forEach(s => s.markers.forEach(m => all.push({ marker: m, category: s.label })));
    const filtered = needle
      ? all.filter(({ marker }) =>
          marker.label.toLowerCase().includes(needle) ||
          marker.code.toLowerCase().includes(needle))
      : all;
    return {
      abnormal: filtered.filter(x => x.marker.flag),
      normal: filtered.filter(x => !x.marker.flag),
    };
  }, [sections, needle]);

  if (!sections?.length) return null;
  const isSearching = needle.length > 0;
  const visibleNormal = isSearching || showNormal ? normal : [];

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
        {isSearching && <span className="bio-search-count">{abnormal.length + normal.length} résultat{(abnormal.length + normal.length) > 1 ? "s" : ""}</span>}
      </div>

      {abnormal.length > 0 && (
        <div className="bio-section">
          <header className="bio-section-head">
            <h3>Hors plage</h3>
            <span className="bio-section-count bio-section-flag">{abnormal.length}</span>
          </header>
          <ul className="bio-marker-list">
            {abnormal.map(({ marker, category }) => (
              <li key={marker.code} className="bio-marker-group">
                <span className="bio-marker-category">{category}</span>
                <MarkerRow m={marker} />
              </li>
            ))}
          </ul>
        </div>
      )}

      {!isSearching && (
        <button
          type="button"
          className="bio-normal-toggle"
          onClick={() => setShowNormal(v => !v)}
          aria-expanded={showNormal}
        >
          {showNormal ? "Masquer" : "Afficher"} les {normal.length} marqueurs normaux
        </button>
      )}

      {visibleNormal.length > 0 && (
        <div className="bio-section">
          <header className="bio-section-head">
            <h3>{isSearching ? "Résultats" : "Normaux"}</h3>
            <span className="bio-section-count">{visibleNormal.length}</span>
          </header>
          <ul className="bio-marker-list">
            {visibleNormal.map(({ marker, category }) => (
              <li key={marker.code} className="bio-marker-group">
                <span className="bio-marker-category">{category}</span>
                <MarkerRow m={marker} />
              </li>
            ))}
          </ul>
        </div>
      )}

      {isSearching && abnormal.length + normal.length === 0 && (
        <p className="bio-search-empty">Aucun marqueur ne contient « {q} ».</p>
      )}
    </section>
  );
}
