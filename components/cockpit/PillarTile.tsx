import Link from "next/link";
import type { PillarTile as Pillar } from "@/lib/types";

interface Props { pillar: Pillar }

export function PillarTile({ pillar }: Props) {
  const id = `t-${pillar.key}`;
  return (
    <Link href={`/detail/${pillar.key}`} className="tile tile--link" aria-labelledby={id}>
      <header className="tile-head">
        <span className="label" id={id}>{pillar.label} <span className="card-chevron" aria-hidden>›</span></span>
        <span className="meta">{pillar.meta}</span>
      </header>
      <p className="tile-figure">{pillar.figure}<span className="unit">{pillar.unit}</span></p>
      <div className="tile-chart">
        {renderChart(pillar)}
      </div>
    </Link>
  );
}

function renderChart(pillar: Pillar) {
  const c = pillar.chart;
  if (c.kind === "area") {
    const linePath = c.points.map((p, i) => `${i === 0 ? "M" : "L"} ${p[0]} ${p[1]}`).join(" ");
    const fillPath = `${linePath} L ${c.points[c.points.length-1][0]} 78 L ${c.points[0][0]} 78 Z`;
    const last = c.points[c.points.length-1];
    const targetY = c.target_y;
    const targetTextY = targetY < 20 ? targetY + 12 : targetY - 4;
    return (
      <svg viewBox="0 0 200 80" preserveAspectRatio="none" role="img" aria-label={`${pillar.label} trend, ${c.target_label}`}>
        <line x1="0" y1={targetY} x2="200" y2={targetY} strokeWidth="0.6"
              strokeDasharray="2 3" opacity="0.5" style={{ stroke: "var(--sage-ash)" }}/>
        <text x="196" y={targetTextY} textAnchor="end" fontSize="9"
              style={{ fill: "var(--sage-ash)", fontVariantNumeric: "tabular-nums" }}>{c.target_label}</text>
        <path d={fillPath} opacity="0.12" style={{ fill: "var(--deep-sage)" }}/>
        <path d={linePath} fill="none" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"
              style={{ stroke: "var(--deep-sage)" }}/>
        <circle cx={last[0]} cy={last[1]} r="2.6" style={{ fill: "var(--deep-sage)" }}/>
      </svg>
    );
  }
  // bars
  const n = c.values.length;
  const slot = 200 / n;
  const barW = slot - 8;
  const ambre = new Set(c.ambre_indices ?? []);
  const dim = new Set(c.dim_indices ?? []);
  return (
    <svg viewBox="0 0 200 80" preserveAspectRatio="none" role="img" aria-label={`${pillar.label} bars`}>
      {c.target_band && (
        <rect x="0" y={c.target_band.y} width="200" height={c.target_band.h} opacity="0.10"
              style={{ fill: "var(--deep-sage)" }}/>
      )}
      {c.target_y !== undefined && (
        <line x1="0" y1={c.target_y} x2="200" y2={c.target_y} strokeWidth="0.6"
              strokeDasharray="2 3" opacity="0.5" style={{ stroke: "var(--sage-ash)" }}/>
      )}
      {c.target_label && (
        <text x="196" y={(c.target_y ?? 14) - 4} textAnchor="end" fontSize="9"
              style={{ fill: "var(--sage-ash)", fontVariantNumeric: "tabular-nums" }}>{c.target_label}</text>
      )}
      <g>
        {c.values.map((v, i) => {
          const x = 6 + i * slot;
          const y = 72 - v;
          const isAmbre = ambre.has(i);
          const isDim = dim.has(i);
          return (
            <rect
              key={i}
              x={x}
              y={y}
              width={barW}
              height={v}
              rx="2"
              style={{ fill: isAmbre ? "var(--ambre-brule)" : "var(--deep-sage)", opacity: isAmbre ? 0.85 : isDim ? 0.55 : 1 }}
            />
          );
        })}
      </g>
    </svg>
  );
}
