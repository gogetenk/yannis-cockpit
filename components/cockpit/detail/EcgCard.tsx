import type { EcgCard as EcgCardType } from "@/lib/types";

interface Props { ecg: EcgCardType }

export function EcgCard({ ecg }: Props) {
  const stats: { label: string; value: string }[] = [];
  if (ecg.mean_hr !== null) stats.push({ label: "FC", value: `${ecg.mean_hr} bpm` });
  if (ecg.rmssd_ms !== null) stats.push({ label: "RMSSD", value: `${ecg.rmssd_ms} ms` });
  if (ecg.sdnn_ms !== null) stats.push({ label: "SDNN", value: `${ecg.sdnn_ms} ms` });
  stats.push({ label: "QTc", value: ecg.qtc_ms !== null ? `${ecg.qtc_ms} ms` : "—" });

  return (
    <section className="ecg-card" aria-labelledby="ecg-heading">
      <div className="ecg-head">
        <h2 id="ecg-heading" className="section-label">ECG</h2>
        <span className="ecg-meta">{ecg.date_label} · {ecg.n_recordings} enreg.</span>
      </div>
      <div className="ecg-rhythm">
        <span className={"ecg-verdict" + (ecg.afib_ok ? "" : " warn")}>{ecg.rhythm}</span>
        <span className="ecg-afib">{ecg.afib_label}</span>
      </div>
      <dl className="ecg-stats">
        {stats.map((s) => (
          <div className="ecg-stat" key={s.label}>
            <dt>{s.label}</dt>
            <dd>{s.value}</dd>
          </div>
        ))}
      </dl>
      <p className="ecg-caveat">
        {ecg.posture_note}
        {ecg.qtc_ms === null ? ` · ${ecg.qtc_label}` : ""}
      </p>
    </section>
  );
}
