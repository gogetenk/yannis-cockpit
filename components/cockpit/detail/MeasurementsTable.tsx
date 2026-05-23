import type { MeasurementRow } from "@/lib/types";

interface Props { rows: MeasurementRow[] }

export function MeasurementsTable({ rows }: Props) {
  if (!rows.length) return null;
  return (
    <section className="measurements" aria-labelledby="measurements-heading">
      <h2 id="measurements-heading" className="section-label">Dernières mesures</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col" className="num">Valeur</th>
            <th scope="col" className="num">Δ</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>{r.date}</td>
              <td className="num">{typeof r.value === "number" ? r.value.toString().replace(".", ",") : r.value} <span className="unit">{r.unit}</span></td>
              <td className={"num delta" + (r.off ? " off" : "")}>{r.delta ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
