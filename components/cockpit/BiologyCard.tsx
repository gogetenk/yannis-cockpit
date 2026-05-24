import Link from "next/link";
import type { BiologySummary } from "@/lib/types";

interface Props { bio: BiologySummary }

export function BiologyCard({ bio }: Props) {
  const ageDeltaStr = bio.phenoage_delta >= 0
    ? `+${bio.phenoage_delta.toFixed(1).replace(".", ",")}`
    : `−${Math.abs(bio.phenoage_delta).toFixed(1).replace(".", ",")}`;
  const nextLabel = bio.days_until_next > 0
    ? `dans ${bio.days_until_next} j`
    : `en retard de ${Math.abs(bio.days_until_next)} j`;

  return (
    <Link href="/detail/biology" className="biology biology--link" aria-labelledby="biology-label">
      <div className="biology-top">
        <span className="label label--ink" id="biology-label">Bilan biologique</span>
        <span className="biology-meta">{bio.n_markers} marqueurs · {bio.lab_name}</span>
      </div>
      <div className="biology-figures">
        <div className="biology-fig">
          <div className="biology-fig-value">{bio.phenoage.toFixed(0)} <span className="biology-fig-unit">ans</span></div>
          <div className="biology-fig-label">PhenoAge <span className="biology-fig-delta">{ageDeltaStr} vs chrono</span></div>
        </div>
        <div className="biology-divider" aria-hidden />
        <div className="biology-fig">
          <div className="biology-fig-value">{bio.lifetime_cv_risk_pct} <span className="biology-fig-unit">%</span></div>
          <div className="biology-fig-label">Risque CV à vie <span className="biology-fig-delta">{bio.lifetime_cv_risk_label}</span></div>
        </div>
      </div>
      <div className="biology-foot">
        <span>dernier bilan · <strong>{bio.last_panel_label}</strong></span>
        <span>prochain · <strong>{nextLabel}</strong></span>
      </div>
    </Link>
  );
}
