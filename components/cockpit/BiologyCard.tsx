import Link from "next/link";
import type { BiologySummary } from "@/lib/types";

interface Props { bio: BiologySummary }

// Median 30-year PREVENT risk for European male 35yo ≈ 9% (NHANES + ESC 2021
// reference cohort, approximation). Place this at the visual center (50%).
const PREVENT_MEDIAN = 9;
const PREVENT_MAX = 30;
// Lloyd-Jones lifetime: median for 35yo male ≈ 36% (sub-optimal tier =
// most common bucket, Berry NEJM 2012 redistribution by age).
const LIFETIME_MEDIAN = 36;
const LIFETIME_MAX = 69;

/** Map value to a 0-100% scale where `median` lands on 50%. Asymmetric:
 *  values <median get the left half, >median get the right half. */
function centeredX(v: number, median: number, max: number): number {
  if (v <= median) return Math.max(0, (v / median) * 50);
  return Math.min(100, 50 + ((v - median) / (max - median)) * 50);
}

export function BiologyCard({ bio }: Props) {
  const pvX = (x: number) => centeredX(x, PREVENT_MEDIAN, PREVENT_MAX);
  const ltX = (x: number) => centeredX(x, LIFETIME_MEDIAN, LIFETIME_MAX);
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
      <svg className="biology-cv-gauge" viewBox="0 0 100 14" preserveAspectRatio="none" aria-hidden>
        <defs>
          <linearGradient id="cv-grad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="var(--deep-sage)" stopOpacity="0.45" />
            <stop offset="50%" stopColor="var(--deep-sage)" stopOpacity="0.55" />
            <stop offset="100%" stopColor="var(--ambre-brule)" stopOpacity="0.85" />
          </linearGradient>
        </defs>
        <rect x="0" y="5" width="100" height="4" rx="2" fill="url(#cv-grad)" />
        {/* Median marker (axis tick at 50%) */}
        <line x1="50" y1="2" x2="50" y2="12" stroke="var(--warm-cream-rest)" strokeWidth="0.8" />
        <line x1="50" y1="11" x2="50" y2="14" stroke="var(--sage-ink)" strokeWidth="0.8" opacity="0.5" />
        <circle cx={pvX(bio.prevent_30y_pct)} cy="7" r="2.4" className="biology-cv-dot" />
      </svg>
      <div className="biology-cv-scale">
        <span className="biology-cv-stop biology-cv-stop--left">faible</span>
        <span className="biology-cv-stop biology-cv-stop--center">médiane 35 ans · {PREVENT_MEDIAN} %</span>
        <span className="biology-cv-stop biology-cv-stop--right">élevé</span>
      </div>

      <div className="biology-cv-row biology-cv-row--secondary">
        <div className="biology-cv-figure biology-cv-figure--small">
          <span className="biology-cv-value-small">{bio.lifetime_cv_risk_pct}</span>
          <span className="biology-cv-unit">% à vie</span>
        </div>
        <span className="biology-cv-sub">Lloyd-Jones · {bio.lifetime_cv_risk_driver ?? bio.lifetime_cv_risk_label}</span>
      </div>
      <svg className="biology-cv-gauge biology-cv-gauge--small" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden>
        <rect x="0" y="3" width="100" height="3" rx="1.5" fill="url(#cv-grad)" />
        <line x1="50" y1="1" x2="50" y2="8" stroke="var(--warm-cream-rest)" strokeWidth="0.6" />
        <line x1="50" y1="7.5" x2="50" y2="10" stroke="var(--sage-ink)" strokeWidth="0.6" opacity="0.5" />
        <circle cx={ltX(bio.lifetime_cv_risk_pct)} cy="4.5" r="1.8" className="biology-cv-dot" />
      </svg>
    </Link>
  );
}
