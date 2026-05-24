import Link from "next/link";
import type { BiologySummary } from "@/lib/types";

interface Props { bio: BiologySummary }

// Lloyd-Jones 2006 / Berry NEJM 2012 reference categories (% lifetime CV risk).
const STOPS = [5, 36, 39, 50, 69];
const LABELS = ["optimal", "sous-opt.", "élevé", "majeur", "2+ majeurs"];
const MAX = 75;

export function BiologyCard({ bio }: Props) {
  const v = bio.lifetime_cv_risk_pct;
  const xPct = (x: number) => Math.min(100, Math.max(0, (x / MAX) * 100));
  return (
    <Link href="/detail/biology" className="biology biology--link" aria-labelledby="biology-label">
      <div className="biology-top">
        <span className="label label--ink" id="biology-label">Risque cardiovasculaire</span>
        <span className="biology-meta">{bio.lifetime_cv_risk_driver ?? bio.lifetime_cv_risk_label}</span>
      </div>
      <div className="biology-cv-figure">
        <span className="biology-cv-value">{v}</span>
        <span className="biology-cv-unit">% à vie</span>
      </div>
      <svg className="biology-cv-gauge" viewBox="0 0 100 14" preserveAspectRatio="none" aria-hidden>
        <defs>
          <linearGradient id="cv-grad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%"   stopColor="var(--deep-sage)" stopOpacity="0.35" />
            <stop offset="40%"  stopColor="var(--deep-sage)" stopOpacity="0.55" />
            <stop offset="70%"  stopColor="var(--ambre-brule)" stopOpacity="0.6" />
            <stop offset="100%" stopColor="var(--ambre-brule)" stopOpacity="0.85" />
          </linearGradient>
        </defs>
        <rect x="0" y="5" width="100" height="4" rx="2" fill="url(#cv-grad)" />
        {STOPS.map((s, i) => (
          <g key={i}>
            <line x1={xPct(s)} y1="3" x2={xPct(s)} y2="11" stroke="var(--warm-cream-rest)" strokeWidth="0.6" />
          </g>
        ))}
        <circle cx={xPct(v)} cy="7" r="2.4" className="biology-cv-dot" />
      </svg>
      <div className="biology-cv-scale">
        {STOPS.map((s, i) => (
          <span key={i} className="biology-cv-stop" style={{ left: `${xPct(s)}%` }}>
            <span className="biology-cv-stop-val">{s}</span>
            <span className="biology-cv-stop-lbl">{LABELS[i]}</span>
          </span>
        ))}
      </div>
    </Link>
  );
}
