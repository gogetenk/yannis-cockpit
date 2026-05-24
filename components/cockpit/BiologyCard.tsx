import Link from "next/link";
import type { BiologySummary } from "@/lib/types";

interface Props { bio: BiologySummary }

const LIFETIME_STOPS = [5, 36, 39, 50, 69];
const LIFETIME_MAX = 75;
const PREVENT_STOPS = [5, 7.5, 20];
const PREVENT_MAX = 30;

export function BiologyCard({ bio }: Props) {
  const ltX = (x: number) => Math.min(100, Math.max(0, (x / LIFETIME_MAX) * 100));
  const pvX = (x: number) => Math.min(100, Math.max(0, (x / PREVENT_MAX) * 100));
  const bpInfo = bio.sbp_avg && bio.dbp_avg ? `TA ${bio.sbp_avg}/${bio.dbp_avg}` : null;

  return (
    <Link href="/detail/biology" className="biology biology--link" aria-labelledby="biology-label">
      <div className="biology-top">
        <span className="label label--ink" id="biology-label">Risque cardiovasculaire</span>
        {bpInfo && <span className="biology-meta">{bpInfo}</span>}
      </div>

      <div className="biology-cv-row">
        <div className="biology-cv-figure">
          <span className="biology-cv-value">{bio.prevent_30y_pct.toFixed(1).replace(".", ",")}</span>
          <span className="biology-cv-unit">% à 30 ans</span>
        </div>
        <span className="biology-cv-sub">PREVENT · {bio.prevent_30y_band_label}</span>
      </div>
      <svg className="biology-cv-gauge" viewBox="0 0 100 12" preserveAspectRatio="none" aria-hidden>
        <defs>
          <linearGradient id="prev-grad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="var(--deep-sage)" stopOpacity="0.35" />
            <stop offset="50%" stopColor="var(--deep-sage)" stopOpacity="0.55" />
            <stop offset="80%" stopColor="var(--ambre-brule)" stopOpacity="0.7" />
            <stop offset="100%" stopColor="var(--ambre-brule)" stopOpacity="0.9" />
          </linearGradient>
        </defs>
        <rect x="0" y="4" width="100" height="4" rx="2" fill="url(#prev-grad)" />
        {PREVENT_STOPS.map((s, i) => (
          <line key={i} x1={pvX(s)} y1="2" x2={pvX(s)} y2="10" stroke="var(--warm-cream-rest)" strokeWidth="0.6" />
        ))}
        <circle cx={pvX(bio.prevent_30y_pct)} cy="6" r="2.2" className="biology-cv-dot" />
      </svg>
      <div className="biology-cv-scale">
        {PREVENT_STOPS.map((s, i) => (
          <span key={i} className="biology-cv-stop" style={{ left: `${pvX(s)}%` }}>
            <span className="biology-cv-stop-val">{s}</span>
          </span>
        ))}
      </div>

      <div className="biology-cv-row biology-cv-row--secondary">
        <div className="biology-cv-figure biology-cv-figure--small">
          <span className="biology-cv-value-small">{bio.lifetime_cv_risk_pct}</span>
          <span className="biology-cv-unit">% à vie</span>
        </div>
        <span className="biology-cv-sub">Lloyd-Jones · {bio.lifetime_cv_risk_driver ?? bio.lifetime_cv_risk_label}</span>
      </div>
      <svg className="biology-cv-gauge biology-cv-gauge--small" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden>
        <rect x="0" y="3" width="100" height="3" rx="1.5" fill="url(#prev-grad)" />
        {LIFETIME_STOPS.map((s, i) => (
          <line key={i} x1={ltX(s)} y1="1.5" x2={ltX(s)} y2="7.5" stroke="var(--warm-cream-rest)" strokeWidth="0.5" />
        ))}
        <circle cx={ltX(bio.lifetime_cv_risk_pct)} cy="4.5" r="1.8" className="biology-cv-dot" />
      </svg>
    </Link>
  );
}
