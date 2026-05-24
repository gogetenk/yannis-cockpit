import Link from "next/link";
import type { BiologySummary } from "@/lib/types";

interface Props { bio: BiologySummary }

export function BiologyCard({ bio }: Props) {
  return (
    <Link href="/detail/biology" className="biology biology--link" aria-labelledby="biology-label">
      <div className="biology-top">
        <span className="label label--ink" id="biology-label">Bilan biologique</span>
        <span className="biology-meta">{bio.n_markers} marqueurs · prochain dans {bio.days_until_next} j</span>
      </div>
      <div className="biology-figures">
        <div className="biology-fig">
          <div className="biology-fig-value">{bio.phenoage.toFixed(0)} <span className="biology-fig-unit">ans</span></div>
          <div className="biology-fig-label">PhenoAge</div>
        </div>
        <div className="biology-divider" aria-hidden />
        <div className="biology-fig">
          <div className="biology-fig-value">{bio.lifetime_cv_risk_pct} <span className="biology-fig-unit">%</span></div>
          <div className="biology-fig-label">Risque CV à vie</div>
        </div>
      </div>
    </Link>
  );
}
