import Link from "next/link";
import type { BiologySummary } from "@/lib/types";

interface Props { bio: BiologySummary }

export function BiologyCard({ bio }: Props) {
  return (
    <Link href="/detail/biology" className="biology biology--link" aria-labelledby="biology-label">
      <div className="biology-top">
        <span className="label label--ink" id="biology-label">Risque cardiovasculaire</span>
        <span className="biology-meta">{bio.n_markers} marqueurs · prochain dans {bio.days_until_next} j</span>
      </div>
      <div className="biology-figures biology-figures--single">
        <div className="biology-fig">
          <div className="biology-fig-value">{bio.lifetime_cv_risk_pct} <span className="biology-fig-unit">%</span></div>
          <div className="biology-fig-label">à vie · {bio.lifetime_cv_risk_label}</div>
        </div>
      </div>
    </Link>
  );
}
