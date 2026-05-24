import Link from "next/link";
import type { BiologySummary } from "@/lib/types";

interface Props { bio: BiologySummary }

const PREVENT_MEDIAN = 9;
const PREVENT_MAX = 30;
const LIFETIME_MEDIAN = 36;
const LIFETIME_MAX = 69;

function centeredX(v: number, median: number, max: number): number {
  if (v <= median) return Math.max(0, (v / median) * 50);
  return Math.min(100, 50 + ((v - median) / (max - median)) * 50);
}

function Gauge({ pctX }: { pctX: number }) {
  return (
    <div className="cv-gauge" aria-hidden>
      <div className="cv-gauge-track" />
      <div className="cv-gauge-median" />
      <div className="cv-gauge-dot" style={{ left: `${pctX}%` }} />
    </div>
  );
}

export function BiologyCard({ bio }: Props) {
  const pvX = centeredX(bio.prevent_30y_pct, PREVENT_MEDIAN, PREVENT_MAX);
  const ltX = centeredX(bio.lifetime_cv_risk_pct, LIFETIME_MEDIAN, LIFETIME_MAX);
  const bpInfo = bio.sbp_avg && bio.dbp_avg ? `TA ${bio.sbp_avg}/${bio.dbp_avg}` : null;

  return (
    <Link href="/detail/biology" className="biology biology--link" aria-labelledby="biology-label">
      <div className="biology-top">
        <span className="label label--ink" id="biology-label">Risque cardiovasculaire <span className="card-chevron" aria-hidden>›</span></span>
        {bpInfo && <span className="biology-meta">{bpInfo}</span>}
      </div>

      <div className="biology-cv-row">
        <div className="biology-cv-figure">
          <span className="biology-cv-value">{bio.prevent_30y_pct.toFixed(1).replace(".", ",")}</span>
          <span className="biology-cv-unit">% à 30 ans</span>
        </div>
        <span className="biology-cv-sub">PREVENT · {bio.prevent_30y_band_label}</span>
      </div>
      <Gauge pctX={pvX} />

      <div className="biology-cv-row biology-cv-row--secondary">
        <div className="biology-cv-figure">
          <span className="biology-cv-value">{bio.lifetime_cv_risk_pct}</span>
          <span className="biology-cv-unit">% à vie</span>
        </div>
        <span className="biology-cv-sub">Lloyd-Jones · {bio.lifetime_cv_risk_driver ?? bio.lifetime_cv_risk_label}</span>
      </div>
      <Gauge pctX={ltX} />
    </Link>
  );
}
